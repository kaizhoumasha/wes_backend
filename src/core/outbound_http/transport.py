"""HTTPX 出站请求的单次发送实现。"""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.core.bounded_http_response import (
    HttpChunkBudgetExceeded,
    HttpCompressionRatioExceeded,
    HttpContentEncodingFailure,
    HttpDecodedBodyBudgetExceeded,
    HttpResponseContractError,
    HttpUnsupportedContentEncoding,
    HttpWireBudgetExceeded,
    decode_bounded_http_body,
    read_bounded_wire_body,
)
from src.core.outbound_http.contracts import (
    OutboundHttpClosedError,
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpRequest,
    OutboundHttpResult,
)

logger = logging.getLogger(__name__)


class _ResponseHeaderLimitExceeded(ValueError):
    """响应 Header 在转换为公开字符串前超过预算。"""


class _HttpxOutboundHttpTransport:
    """持有一个 HTTPX Client 的最小出站传输实现。"""

    def __init__(self, *, client: httpx.AsyncClient, system_id: str, timeout_seconds: float) -> None:
        self._client = client
        self._system_id = system_id
        self._timeout_seconds = timeout_seconds
        self._cleanup_timeout_seconds = min(timeout_seconds, 1.0)
        self._closed = False

    async def send(self, request: OutboundHttpRequest) -> OutboundHttpResult:  # noqa: PLR0912 - 冻结异常矩阵逐项映射。
        """发送一次请求，只返回可确认的传输事实。"""

        if self._closed:
            raise OutboundHttpClosedError("outbound HTTP transport is closed")

        response: httpx.Response | None = None
        response_headers: tuple[tuple[str, str], ...] = ()
        status_code: int | None = None
        result: OutboundHttpResult | None = None
        cleanup_failed = False
        started_at = asyncio.get_running_loop().time()
        deadline = started_at + self._timeout_seconds

        try:
            async with asyncio.timeout_at(deadline):
                outbound_request = self._client.build_request(
                    request.method.value,
                    request.path,
                    params=request.query,
                    headers=request.headers,
                    content=request.body,
                )
                response = await self._client.send(outbound_request, stream=True)
                status_code = int(response.status_code)
                response_headers = _bounded_response_headers(response, request=request)
                raw_body, _ = await read_bounded_wire_body(
                    response,
                    max_chunk_bytes=request.response_limits.max_chunk_bytes,
                    max_wire_bytes=request.response_limits.max_wire_bytes,
                    cumulative_wire_bytes=0,
                )
                decoded_body = decode_bounded_http_body(
                    raw_body,
                    content_encoding=response.headers.get("content-encoding", "identity"),
                    allowed_content_encodings=request.response_limits.allowed_content_encodings,
                    max_decoded_bytes=request.response_limits.max_decoded_bytes,
                    max_compression_ratio=request.response_limits.max_compression_ratio,
                )
                result = OutboundHttpResult(
                    delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                    status_code=status_code,
                    response_headers=response_headers,
                    decoded_body=decoded_body,
                )
        except httpx.PoolTimeout:
            result = _not_sent(OutboundHttpFailureKind.POOL_TIMEOUT)
        except httpx.ConnectTimeout:
            result = _not_sent(OutboundHttpFailureKind.CONNECT_TIMEOUT)
        except httpx.ConnectError:
            result = _not_sent(OutboundHttpFailureKind.CONNECT_ERROR)
        except httpx.WriteTimeout:
            result = _delivery_unknown(OutboundHttpFailureKind.WRITE_TIMEOUT)
        except httpx.WriteError:
            result = _delivery_unknown(OutboundHttpFailureKind.WRITE_ERROR)
        except httpx.ReadTimeout:
            result = _read_failure(
                response=response,
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.READ_TIMEOUT,
            )
        except httpx.ReadError:
            result = _read_failure(
                response=response,
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.READ_ERROR,
            )
        except httpx.RemoteProtocolError:
            result = _read_failure(
                response=response,
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.REMOTE_PROTOCOL_ERROR,
            )
        except TimeoutError:
            result = _read_failure(
                response=response,
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.TOTAL_TIMEOUT,
            )
        except _ResponseHeaderLimitExceeded:
            result = _response_failure(
                status_code=status_code,
                response_headers=(),
                failure_kind=OutboundHttpFailureKind.RESPONSE_HEADER_LIMIT_EXCEEDED,
            )
        except HttpResponseContractError:
            result = _response_failure(
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.RESPONSE_METADATA_INVALID,
            )
        except HttpChunkBudgetExceeded:
            result = _response_failure(
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.RESPONSE_CHUNK_LIMIT_EXCEEDED,
            )
        except HttpWireBudgetExceeded:
            result = _response_failure(
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.RESPONSE_WIRE_LIMIT_EXCEEDED,
            )
        except HttpDecodedBodyBudgetExceeded:
            result = _response_failure(
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.RESPONSE_DECODED_LIMIT_EXCEEDED,
            )
        except HttpCompressionRatioExceeded:
            result = _response_failure(
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.RESPONSE_COMPRESSION_RATIO_EXCEEDED,
            )
        except HttpUnsupportedContentEncoding:
            result = _response_failure(
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_UNSUPPORTED,
            )
        except HttpContentEncodingFailure:
            result = _response_failure(
                status_code=status_code,
                response_headers=response_headers,
                failure_kind=OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_INVALID,
            )
        finally:
            if response is not None:
                cleanup_failed = await self._cleanup_response(response)
                if cleanup_failed:
                    result = _apply_cleanup_failure(
                        result=result,
                        status_code=status_code,
                        response_headers=response_headers,
                    )

        if result is None:
            raise RuntimeError("outbound HTTP transport returned without a result")
        self._log_result(request=request, result=result, started_at=started_at, cleanup_failed=cleanup_failed)
        return result

    async def aclose(self) -> None:
        """关闭持有的 Client；重复关闭不产生额外副作用。"""

        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def _cleanup_response(self, response: httpx.Response) -> bool:
        cleanup_task = asyncio.create_task(response.aclose())
        cleanup_wait = asyncio.gather(cleanup_task, return_exceptions=True)
        try:
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                outcomes = await asyncio.shield(cleanup_wait)
        except TimeoutError:
            return True
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(self._cleanup_timeout_seconds):
                    await asyncio.shield(cleanup_wait)
            except TimeoutError:
                pass
            raise

        outcome = outcomes[0]
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        return isinstance(outcome, BaseException)

    def _log_result(
        self,
        *,
        request: OutboundHttpRequest,
        result: OutboundHttpResult,
        started_at: float,
        cleanup_failed: bool,
    ) -> None:
        logger.info(
            "outbound_http_send system_id=%s method=%s delivery_state=%s status_code=%s "
            "failure_kind=%s cleanup_failed=%s duration_ms=%d",
            self._system_id,
            request.method.value,
            result.delivery_state.value,
            result.status_code,
            result.failure_kind.value if result.failure_kind is not None else None,
            cleanup_failed,
            int((asyncio.get_running_loop().time() - started_at) * 1000),
        )


def _bounded_response_headers(
    response: httpx.Response,
    *,
    request: OutboundHttpRequest,
) -> tuple[tuple[str, str], ...]:
    raw_headers = response.headers.raw
    if len(raw_headers) > request.response_limits.max_response_header_count:
        raise _ResponseHeaderLimitExceeded
    if (
        sum(len(name) + len(value) for name, value in raw_headers)
        > request.response_limits.max_response_header_wire_bytes
    ):
        raise _ResponseHeaderLimitExceeded
    return tuple((name.decode("latin-1"), value.decode("latin-1")) for name, value in raw_headers)


def _not_sent(failure_kind: OutboundHttpFailureKind) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.NOT_SENT,
        failure_kind=failure_kind,
    )


def _delivery_unknown(failure_kind: OutboundHttpFailureKind) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
        failure_kind=failure_kind,
    )


def _read_failure(
    *,
    response: httpx.Response | None,
    status_code: int | None,
    response_headers: tuple[tuple[str, str], ...],
    failure_kind: OutboundHttpFailureKind,
) -> OutboundHttpResult:
    if response is None:
        return _delivery_unknown(failure_kind)
    return _response_failure(
        status_code=status_code,
        response_headers=response_headers,
        failure_kind=failure_kind,
    )


def _response_failure(
    *,
    status_code: int | None,
    response_headers: tuple[tuple[str, str], ...],
    failure_kind: OutboundHttpFailureKind,
) -> OutboundHttpResult:
    if status_code is None:
        raise RuntimeError("response failure requires a status code")
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status_code,
        response_headers=response_headers,
        failure_kind=failure_kind,
    )


def _apply_cleanup_failure(
    *,
    result: OutboundHttpResult | None,
    status_code: int | None,
    response_headers: tuple[tuple[str, str], ...],
) -> OutboundHttpResult | None:
    if result is None:
        logger.warning("outbound_http_cleanup_failed cleanup_failed=True")
        return None
    if result.failure_kind is not None:
        logger.warning(
            "outbound_http_cleanup_failed delivery_state=%s status_code=%s failure_kind=%s cleanup_failed=True",
            result.delivery_state.value,
            result.status_code,
            result.failure_kind.value,
        )
        return result
    return _response_failure(
        status_code=status_code,
        response_headers=response_headers,
        failure_kind=OutboundHttpFailureKind.RESPONSE_CLEANUP_FAILED,
    )
