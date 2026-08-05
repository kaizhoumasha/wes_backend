from __future__ import annotations

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
    from collections.abc import Callable


def _capture_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
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
