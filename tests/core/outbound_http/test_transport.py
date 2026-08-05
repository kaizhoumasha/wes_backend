from __future__ import annotations

import asyncio
import gzip
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from src.core.outbound_http.contracts import (
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpResponseLimits,
)
from src.core.outbound_http.transport import _HttpxOutboundHttpTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: tuple[bytes, ...] = (),
        *,
        read_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._read_error = read_error
        self._close_error = close_error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._read_error is not None:
            raise self._read_error

    async def aclose(self) -> None:
        if self._close_error is not None:
            raise self._close_error


class _BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.entered.set()
        await self.release.wait()
        yield b"body"

    async def aclose(self) -> None:
        self.closed.set()


def _request(**kwargs: object) -> OutboundHttpRequest:
    return OutboundHttpRequest(method=OutboundHttpMethod.POST, path="/v1/items", **kwargs)


def _transport(
    handler: Callable[[httpx.Request], httpx.Response] | Callable[[httpx.Request], object],
    *,
    timeout_seconds: float = 1.0,
) -> _HttpxOutboundHttpTransport:
    client = httpx.AsyncClient(
        base_url="https://provider.test",
        transport=httpx.MockTransport(handler),
    )
    return _HttpxOutboundHttpTransport(
        client=client,
        system_id="wms",
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 302, 404, 503])
async def test_send_uses_one_request_and_preserves_all_http_statuses_as_transport_facts(status_code: int) -> None:
    received_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        return httpx.Response(
            status_code,
            headers=[("set-cookie", "first"), ("set-cookie", "second")],
            stream=_ChunkStream((b"not found",)),
            request=request,
        )

    transport = _transport(handler)
    try:
        result = await transport.send(_request(query=(("tag", "one"), ("tag", "two"))))
    finally:
        await transport.aclose()

    assert len(received_requests) == 1
    assert received_requests[0].url.query == b"tag=one&tag=two"
    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == status_code
    assert result.response_headers == (("set-cookie", "first"), ("set-cookie", "second"))
    assert result.decoded_body == b"not found"
    assert result.failure_kind is None


@pytest.mark.asyncio
async def test_concurrent_sends_reuse_the_transport_owned_client() -> None:
    received_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        return httpx.Response(204, request=request)

    transport = _transport(handler)
    try:
        first, second = await asyncio.gather(transport.send(_request()), transport.send(_request()))
    finally:
        await transport.aclose()

    assert len(received_requests) == 2
    assert first.status_code == 204
    assert second.status_code == 204


@pytest.mark.asyncio
async def test_send_decodes_only_the_bounded_wire_body() -> None:
    compressed_body = gzip.compress(b"decoded payload")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            headers={"content-encoding": "gzip"},
            stream=_ChunkStream((compressed_body,)),
            request=request,
        )

    transport = _transport(handler)
    try:
        result = await transport.send(_request())
    finally:
        await transport.aclose()

    assert result.decoded_body == b"decoded payload"
    assert compressed_body not in (result.decoded_body,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_state", "expected_failure"),
    [
        (httpx.PoolTimeout("pool"), OutboundHttpDeliveryState.NOT_SENT, OutboundHttpFailureKind.POOL_TIMEOUT),
        (httpx.ConnectTimeout("connect"), OutboundHttpDeliveryState.NOT_SENT, OutboundHttpFailureKind.CONNECT_TIMEOUT),
        (httpx.ConnectError("connect"), OutboundHttpDeliveryState.NOT_SENT, OutboundHttpFailureKind.CONNECT_ERROR),
        (
            httpx.WriteTimeout("write"),
            OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
            OutboundHttpFailureKind.WRITE_TIMEOUT,
        ),
        (httpx.WriteError("write"), OutboundHttpDeliveryState.DELIVERY_UNKNOWN, OutboundHttpFailureKind.WRITE_ERROR),
    ],
)
async def test_send_classifies_pre_response_httpx_failures(
    error: Exception,
    expected_state: OutboundHttpDeliveryState,
    expected_failure: OutboundHttpFailureKind,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    transport = _transport(handler)
    try:
        result = await transport.send(_request())
    finally:
        await transport.aclose()

    assert result.delivery_state is expected_state
    assert result.failure_kind is expected_failure
    assert result.status_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_failure"),
    [
        (httpx.ReadTimeout("read"), OutboundHttpFailureKind.READ_TIMEOUT),
        (httpx.ReadError("read"), OutboundHttpFailureKind.READ_ERROR),
        (httpx.RemoteProtocolError("protocol"), OutboundHttpFailureKind.REMOTE_PROTOCOL_ERROR),
    ],
)
async def test_send_classifies_pre_response_read_and_protocol_failures_as_unknown(
    error: Exception,
    expected_failure: OutboundHttpFailureKind,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    transport = _transport(handler)
    try:
        result = await transport.send(_request())
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.DELIVERY_UNKNOWN
    assert result.failure_kind is expected_failure


@pytest.mark.asyncio
async def test_send_preserves_response_received_when_body_read_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_ChunkStream(read_error=httpx.ReadError("read")),
            request=request,
        )

    transport = _transport(handler)
    try:
        result = await transport.send(_request())
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 200
    assert result.decoded_body is None
    assert result.failure_kind is OutboundHttpFailureKind.READ_ERROR


@pytest.mark.asyncio
async def test_send_maps_header_budget_failure_without_exposing_partial_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[("x-one", "1"), ("x-two", "2")],
            content=b"body",
            request=request,
        )

    transport = _transport(handler)
    limits = OutboundHttpResponseLimits(max_response_header_count=1)
    try:
        result = await transport.send(_request(response_limits=limits))
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 200
    assert result.response_headers == ()
    assert result.failure_kind is OutboundHttpFailureKind.RESPONSE_HEADER_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_send_maps_invalid_response_metadata_after_receiving_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "not-an-integer"},
            stream=_ChunkStream((b"body",)),
            request=request,
        )

    transport = _transport(handler)
    try:
        result = await transport.send(_request())
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 200
    assert result.decoded_body is None
    assert result.failure_kind is OutboundHttpFailureKind.RESPONSE_METADATA_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "limits", "expected_failure"),
    [
        (
            lambda request: httpx.Response(
                200,
                stream=_ChunkStream((b"too long",)),
                request=request,
            ),
            OutboundHttpResponseLimits(max_chunk_bytes=2),
            OutboundHttpFailureKind.RESPONSE_CHUNK_LIMIT_EXCEEDED,
        ),
        (
            lambda request: httpx.Response(200, content=b"body", request=request),
            OutboundHttpResponseLimits(max_wire_bytes=2),
            OutboundHttpFailureKind.RESPONSE_WIRE_LIMIT_EXCEEDED,
        ),
        (
            lambda request: httpx.Response(200, content=b"body", request=request),
            OutboundHttpResponseLimits(max_decoded_bytes=2),
            OutboundHttpFailureKind.RESPONSE_DECODED_LIMIT_EXCEEDED,
        ),
        (
            lambda request: httpx.Response(
                200,
                headers={"content-encoding": "br"},
                stream=_ChunkStream((b"body",)),
                request=request,
            ),
            OutboundHttpResponseLimits(),
            OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_UNSUPPORTED,
        ),
        (
            lambda request: httpx.Response(
                200,
                headers={"content-encoding": "gzip"},
                stream=_ChunkStream((b"not gzip",)),
                request=request,
            ),
            OutboundHttpResponseLimits(),
            OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_INVALID,
        ),
    ],
)
async def test_send_maps_bounded_response_failures(
    response: Callable[[httpx.Request], httpx.Response],
    limits: OutboundHttpResponseLimits,
    expected_failure: OutboundHttpFailureKind,
) -> None:
    transport = _transport(response)
    try:
        result = await transport.send(_request(response_limits=limits))
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 200
    assert result.decoded_body is None
    assert result.failure_kind is expected_failure


@pytest.mark.asyncio
async def test_send_maps_send_total_timeout_to_unknown_delivery() -> None:
    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        return httpx.Response(200, content=b"late", request=request)

    transport = _transport(handler, timeout_seconds=0.001)
    try:
        sending = asyncio.create_task(transport.send(_request()))
        await entered.wait()
        result = await sending
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.DELIVERY_UNKNOWN
    assert result.failure_kind is OutboundHttpFailureKind.TOTAL_TIMEOUT


@pytest.mark.asyncio
async def test_send_maps_response_total_timeout_to_response_received() -> None:
    stream = _BlockingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    transport = _transport(handler, timeout_seconds=0.001)
    try:
        sending = asyncio.create_task(transport.send(_request()))
        await stream.entered.wait()
        result = await sending
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 200
    assert result.failure_kind is OutboundHttpFailureKind.TOTAL_TIMEOUT
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_cancellation_during_send_propagates_before_a_response_exists() -> None:
    send_entered = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        send_entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise
        return httpx.Response(200, request=request)

    transport = _transport(handler)
    sending = asyncio.create_task(transport.send(_request()))
    await send_entered.wait()
    sending.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await sending
    finally:
        await transport.aclose()

    assert send_cancelled.is_set()


@pytest.mark.asyncio
async def test_cancellation_during_response_read_closes_response_and_propagates() -> None:
    stream = _BlockingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    transport = _transport(handler)
    sending = asyncio.create_task(transport.send(_request()))
    await stream.entered.wait()
    sending.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await sending
    finally:
        await transport.aclose()

    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_cancellation_during_cleanup_waits_for_close_and_propagates() -> None:
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    close_cancelled = asyncio.Event()
    close_finished = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=b"body", request=request)
        await response.aread()

        async def blocking_close() -> None:
            close_started.set()
            try:
                await close_release.wait()
            except asyncio.CancelledError:
                close_cancelled.set()
                raise
            finally:
                close_finished.set()

        response.aclose = blocking_close  # type: ignore[method-assign]
        return response

    transport = _transport(handler)
    sending = asyncio.create_task(transport.send(_request()))
    await close_started.wait()
    sending.cancel()
    cancellation_delivered = asyncio.Event()
    asyncio.get_running_loop().call_soon(cancellation_delivered.set)
    await cancellation_delivered.wait()
    try:
        assert not sending.done()
        close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await sending
    finally:
        close_release.set()
        await close_finished.wait()
        await transport.aclose()

    assert not close_cancelled.is_set()


@pytest.mark.asyncio
async def test_cleanup_failure_becomes_stable_response_failure(caplog: pytest.LogCaptureFixture) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=b"body", request=request)
        await response.aread()

        async def failing_close() -> None:
            raise RuntimeError("cleanup secret")

        response.aclose = failing_close  # type: ignore[method-assign]
        return response

    transport = _transport(handler)
    caplog.set_level("INFO", logger="src.core.outbound_http.transport")
    try:
        result = await transport.send(_request())
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 200
    assert result.decoded_body is None
    assert result.failure_kind is OutboundHttpFailureKind.RESPONSE_CLEANUP_FAILED
    assert "cleanup_failed=True" in caplog.text
    assert "cleanup secret" not in caplog.text


@pytest.mark.asyncio
async def test_cleanup_timeout_cancels_and_joins_response_close_task() -> None:
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    close_cancelled = asyncio.Event()
    close_finished = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=b"body", request=request)
        await response.aread()

        async def blocking_close() -> None:
            close_started.set()
            try:
                await close_release.wait()
            except asyncio.CancelledError:
                close_cancelled.set()
                raise
            finally:
                close_finished.set()

        response.aclose = blocking_close  # type: ignore[method-assign]
        return response

    transport = _transport(handler, timeout_seconds=0.01)
    try:
        result = await transport.send(_request())

        assert close_started.is_set()
        assert close_cancelled.is_set()
        assert close_finished.is_set()
        assert result.failure_kind is OutboundHttpFailureKind.RESPONSE_CLEANUP_FAILED
    finally:
        close_release.set()
        await close_finished.wait()
        await transport.aclose()


@pytest.mark.asyncio
async def test_primary_failure_is_not_replaced_when_cleanup_also_fails(caplog: pytest.LogCaptureFixture) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(
            200,
            headers=[("x-one", "1"), ("x-two", "2")],
            content=b"body",
            request=request,
        )

        async def failing_close() -> None:
            raise RuntimeError("cleanup secret")

        response.aclose = failing_close  # type: ignore[method-assign]
        return response

    transport = _transport(handler)
    limits = OutboundHttpResponseLimits(max_response_header_count=1)
    try:
        result = await transport.send(_request(response_limits=limits))
    finally:
        await transport.aclose()

    assert result.failure_kind is OutboundHttpFailureKind.RESPONSE_HEADER_LIMIT_EXCEEDED
    assert "cleanup_failed=True" in caplog.text
    assert "cleanup secret" not in caplog.text


@pytest.mark.asyncio
async def test_send_maps_compression_ratio_budget_failure_after_receiving_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=_ChunkStream((gzip.compress(b"body" * 256),)),
            request=request,
        )

    transport = _transport(handler)
    limits = OutboundHttpResponseLimits(max_compression_ratio=1.0)
    try:
        result = await transport.send(_request(response_limits=limits))
    finally:
        await transport.aclose()

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 200
    assert result.decoded_body is None
    assert result.failure_kind is OutboundHttpFailureKind.RESPONSE_COMPRESSION_RATIO_EXCEEDED


@pytest.mark.asyncio
async def test_send_propagates_unknown_programming_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected")

    transport = _transport(handler)
    try:
        with pytest.raises(RuntimeError, match="unexpected"):
            await transport.send(_request())
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_send_logs_only_stable_non_sensitive_transport_facts(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"response-secret", request=request)

    transport = _transport(handler)
    caplog.set_level("INFO", logger="src.core.outbound_http.transport")
    try:
        await transport.send(
            _request(
                query=(("token", "query-secret"),),
                headers=(("Authorization", "header-secret"),),
                body=b"body-secret",
            )
        )
    finally:
        await transport.aclose()

    transport_logs = "\n".join(
        record.getMessage() for record in caplog.records if record.name == "src.core.outbound_http.transport"
    )
    assert "query-secret" not in transport_logs
    assert "header-secret" not in transport_logs
    assert "body-secret" not in transport_logs
    assert "response-secret" not in transport_logs
    assert "system_id=wms" in transport_logs
    assert "method=POST" in transport_logs


def test_transport_implementation_does_not_hide_unknown_errors_with_catch_all() -> None:
    source = Path("src/core/outbound_http/transport.py").read_text(encoding="utf-8")

    assert "except Exception" not in source
