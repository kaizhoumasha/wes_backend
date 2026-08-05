from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING

import httpx
import pytest

from src.core.outbound_http import build_outbound_http_transport
from src.core.outbound_http.contracts import (
    OutboundHttpClosedError,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpRequestError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


def _capture_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]] | None = None,
) -> tuple[list[dict[str, object]], list[httpx.AsyncClient]]:
    async_client_type = httpx.AsyncClient
    captured_kwargs: list[dict[str, object]] = []
    clients: list[httpx.AsyncClient] = []

    def create_client(**kwargs: object) -> httpx.AsyncClient:
        captured_kwargs.append(kwargs)
        client = async_client_type(
            transport=httpx.MockTransport(handler or (lambda request: httpx.Response(204, request=request))),
            **kwargs,
        )
        clients.append(client)
        return client

    monkeypatch.setattr("src.core.outbound_http.factory.httpx.AsyncClient", create_client)
    return captured_kwargs, clients


@pytest.mark.asyncio
async def test_builder_creates_a_configured_transport_for_a_stable_system_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs, clients = _capture_client(monkeypatch)

    transport = build_outbound_http_transport(
        system_id="wms-primary_1",
        base_url="https://provider.test/",
        timeout_seconds=3.5,
    )
    try:
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["base_url"] == "https://provider.test/"
        assert captured_kwargs[0]["trust_env"] is False
        assert captured_kwargs[0]["follow_redirects"] is False
        timeout = captured_kwargs[0]["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == timeout.read == timeout.write == timeout.pool == 3.5
    finally:
        await transport.aclose()

    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_builder_accepts_a_plain_http_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs, _ = _capture_client(monkeypatch)

    transport = build_outbound_http_transport(
        system_id="ecs",
        base_url="http://provider.test:8080/",
        timeout_seconds=1,
    )
    try:
        assert captured_kwargs[0]["base_url"] == "http://provider.test:8080/"
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_builder_does_not_reuse_response_cookies_for_later_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    received_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        headers = {"set-cookie": "session=response-secret"} if len(received_requests) == 1 else {}
        return httpx.Response(204, headers=headers, request=request)

    _capture_client(monkeypatch, handler)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    try:
        request = OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health")
        await transport.send(request)
        await transport.send(request)
    finally:
        await transport.aclose()

    assert "cookie" not in received_requests[1].headers


@pytest.mark.asyncio
async def test_builder_does_not_inject_response_cookies_into_concurrent_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_response_reading = asyncio.Event()
    release_first_response = asyncio.Event()
    received_requests: list[httpx.Request] = []

    class BlockingResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            first_response_reading.set()
            await release_first_response.wait()
            yield b""

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        if len(received_requests) == 1:
            return httpx.Response(
                200,
                headers={"set-cookie": "session=response-secret"},
                stream=BlockingResponseStream(),
                request=request,
            )
        return httpx.Response(204, request=request)

    _capture_client(monkeypatch, handler)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    first_sending = asyncio.create_task(
        transport.send(OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health"))
    )
    try:
        await first_response_reading.wait()
        await transport.send(OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health"))
        release_first_response.set()
        await first_sending
    finally:
        release_first_response.set()
        await transport.aclose()

    assert "cookie" not in received_requests[1].headers


@pytest.mark.asyncio
async def test_builder_advertises_only_supported_response_content_encodings(monkeypatch: pytest.MonkeyPatch) -> None:
    received_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        return httpx.Response(204, request=request)

    _capture_client(monkeypatch, handler)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    try:
        await transport.send(OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health"))
    finally:
        await transport.aclose()

    assert received_requests[0].headers["accept-encoding"] == "gzip, deflate"


@pytest.mark.asyncio
async def test_builder_suppresses_httpx_info_request_url_logs(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture_client(monkeypatch)
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)
    caplog.set_level(logging.INFO)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    try:
        await transport.send(
            OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health", query=(("token", "query-secret"),))
        )
    finally:
        await transport.aclose()
        httpx_logger.setLevel(original_level)

    assert "query-secret" not in caplog.text


@pytest.mark.asyncio
async def test_builder_does_not_change_the_process_httpx_logger_level(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_client(monkeypatch)
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    try:
        assert httpx_logger.level == logging.INFO
    finally:
        await transport.aclose()
        httpx_logger.setLevel(original_level)


@pytest.mark.asyncio
async def test_outbound_log_suppression_does_not_hide_concurrent_unrelated_httpx_info(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_entered = asyncio.Event()
    release_response = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        request_entered.set()
        await release_response.wait()
        return httpx.Response(204, request=request)

    _capture_client(monkeypatch, handler)
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)
    caplog.set_level(logging.INFO)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    sending = asyncio.create_task(
        transport.send(
            OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health", query=(("token", "query-secret"),))
        )
    )
    try:
        await request_entered.wait()
        httpx_logger.info("unrelated_httpx_fact")
        release_response.set()
        await sending
    finally:
        release_response.set()
        await transport.aclose()
        httpx_logger.setLevel(original_level)

    assert "unrelated_httpx_fact" in caplog.text
    assert "query-secret" not in caplog.text


@pytest.mark.asyncio
async def test_outbound_log_suppression_hides_httpcore_headers_without_hiding_unrelated_logs(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_entered = asyncio.Event()
    release_response = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpcore.http11").debug(
            "receive_response_headers.complete return_value=%r",
            [(b"set-cookie", b"session=response-secret")],
        )
        request_entered.set()
        await release_response.wait()
        return httpx.Response(204, request=request)

    _capture_client(monkeypatch, handler)
    caplog.set_level(logging.DEBUG, logger="httpcore.http11")
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    sending = asyncio.create_task(transport.send(OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health")))
    try:
        await request_entered.wait()
        logging.getLogger("httpcore.http11").debug("unrelated_httpcore_fact")
        release_response.set()
        await sending
    finally:
        release_response.set()
        await transport.aclose()

    assert "response-secret" not in caplog.text
    assert "unrelated_httpcore_fact" in caplog.text


@pytest.mark.parametrize(
    "system_id",
    ["", "WMS", "wms space", "wms\nprimary", "a" * 65],
)
def test_builder_rejects_non_stable_system_id(system_id: str) -> None:
    with pytest.raises(OutboundHttpRequestError, match="system_id"):
        build_outbound_http_transport(
            system_id=system_id,
            base_url="https://provider.test/",
            timeout_seconds=1,
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https:///",
        "ftp://provider.test/",
        "https://provider.test/api",
        "https://user:password@provider.test/",
        "https://provider.test/?page=1",
        "https://provider.test/#section",
        "https://provider.test/\n",
        "https://provider.test:not-a-port/",
    ],
)
def test_builder_rejects_ambiguous_or_non_http_base_url(base_url: str) -> None:
    with pytest.raises(OutboundHttpRequestError, match="base URL"):
        build_outbound_http_transport(
            system_id="wms",
            base_url=base_url,
            timeout_seconds=1,
        )


@pytest.mark.parametrize("timeout_seconds", [0, -1, math.inf, math.nan, True, "1"])
def test_builder_rejects_non_positive_or_non_numeric_timeout(timeout_seconds: object) -> None:
    with pytest.raises(OutboundHttpRequestError, match="timeout"):
        build_outbound_http_transport(
            system_id="wms",
            base_url="https://provider.test/",
            timeout_seconds=timeout_seconds,
        )


@pytest.mark.asyncio
async def test_transport_created_by_builder_closes_idempotently_and_rejects_later_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture_client(monkeypatch)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )

    await transport.aclose()
    await transport.aclose()

    with pytest.raises(OutboundHttpClosedError):
        await transport.send(OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health"))


@pytest.mark.asyncio
async def test_transport_close_finishes_resource_release_before_propagating_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, clients = _capture_client(monkeypatch)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    close_completed = asyncio.Event()

    async def blocking_close() -> None:
        close_started.set()
        await release_close.wait()
        close_completed.set()

    clients[0].aclose = blocking_close  # type: ignore[method-assign]
    closing = asyncio.create_task(transport.aclose())
    await close_started.wait()

    closing.cancel()
    await asyncio.sleep(0)

    assert not closing.done()
    release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert close_completed.is_set()


@pytest.mark.asyncio
async def test_transport_created_by_builder_does_not_swallow_close_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _, clients = _capture_client(monkeypatch)
    transport = build_outbound_http_transport(
        system_id="wms",
        base_url="https://provider.test/",
        timeout_seconds=1,
    )
    client = clients[0]
    original_aclose = client.aclose

    async def fail_to_close() -> None:
        raise RuntimeError("close failed")

    client.aclose = fail_to_close  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="close failed"):
        await transport.aclose()

    await original_aclose()
