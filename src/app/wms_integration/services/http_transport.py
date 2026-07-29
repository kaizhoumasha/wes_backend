"""WMS HTTP 调用共用的无 operation 语义 transport primitives。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from src.core.bounded_http_response import read_bounded_wire_body

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class WmsBoundedHttpResponse:
    """关闭 response 后保留的有界、无业务解释 transport 结果。"""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class _BorrowedAsyncTransport(httpx.AsyncBaseTransport):
    """让短生命周期 client 不得关闭调用方拥有的可复用 transport。"""

    def __init__(self, delegate: httpx.AsyncBaseTransport) -> None:
        self._delegate = delegate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._delegate.handle_async_request(request)

    async def aclose(self) -> None:
        return None


def sign_wms_hmac_request(
    request: httpx.Request,
    *,
    credential_reference: str,
    auth_scheme: str,
    secret: bytes,
    now: Callable[[], datetime],
    nonce_factory: Callable[[], str],
) -> None:
    """以现有 WMS HMAC canonical 规则填充封闭认证 header。"""

    if auth_scheme != "HMAC_SHA256" or not credential_reference:
        raise ValueError("WMS HMAC authentication binding is invalid")
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("WMS HMAC secret must be non-empty bytes")
    current_time = now()
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("WMS HMAC signing clock must be timezone-aware")
    timestamp = str(int(current_time.timestamp()))
    nonce = nonce_factory()
    if not isinstance(nonce, str) or not nonce or "\r" in nonce or "\n" in nonce:
        raise ValueError("WMS HMAC nonce must be a non-empty single-line string")
    body_hash = hashlib.sha256(request.content).hexdigest()
    canonical = "\n".join(
        (
            request.method,
            request.url.raw_path.decode("ascii"),
            timestamp,
            nonce,
            body_hash,
        )
    )
    signature = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    request.headers.update(
        {
            "X-WMS-Credential-Reference": credential_reference,
            "X-WMS-Signature-Algorithm": auth_scheme,
            "X-WMS-Timestamp": timestamp,
            "X-WMS-Nonce": nonce,
            "X-WMS-Content-SHA256": body_hash,
            "X-WMS-Signature": signature,
        }
    )


@asynccontextmanager
async def open_wms_http_client(
    *,
    transport: httpx.AsyncBaseTransport | None,
    timeout_seconds: float,
    deadline: float,
) -> AsyncIterator[httpx.AsyncClient]:
    """创建共用 client，并保证关闭也受同一绝对 deadline 约束。"""

    borrowed_transport = _BorrowedAsyncTransport(transport) if transport is not None else None
    client = httpx.AsyncClient(
        transport=borrowed_transport,
        trust_env=False,
        timeout=timeout_seconds,
    )
    try:
        yield client
    finally:
        async with asyncio.timeout_at(deadline):
            await client.aclose()


async def send_bounded_wms_request(
    *,
    client: httpx.AsyncClient,
    request: httpx.Request,
    authenticate: Callable[[httpx.Request], None],
    deadline: float,
    max_chunk_bytes: int,
    max_wire_bytes: int,
    cumulative_wire_bytes: int = 0,
) -> tuple[WmsBoundedHttpResponse, int]:
    """签名、发送并有界读取单个响应；不解释 operation-specific HTTP 语义。"""

    if max_chunk_bytes <= 0 or max_wire_bytes <= 0 or cumulative_wire_bytes < 0:
        raise ValueError("WMS HTTP wire budgets must be positive")
    response: httpx.Response | None = None
    try:
        async with asyncio.timeout_at(deadline):
            authenticate(request)
            response = await client.send(request, stream=True)
            body, cumulative_wire_bytes = await read_bounded_wire_body(
                response,
                max_chunk_bytes=max_chunk_bytes,
                max_wire_bytes=max_wire_bytes,
                cumulative_wire_bytes=cumulative_wire_bytes,
            )
            bounded = WmsBoundedHttpResponse(
                status_code=int(response.status_code),
                headers=httpx.Headers(response.headers),
                body=body,
            )
    finally:
        if response is not None:
            async with asyncio.timeout_at(deadline):
                await response.aclose()
    return bounded, cumulative_wire_bytes


__all__ = [
    "WmsBoundedHttpResponse",
    "open_wms_http_client",
    "send_bounded_wms_request",
    "sign_wms_hmac_request",
]
