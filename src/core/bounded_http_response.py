"""跨域共用的有界 HTTP 响应读取与解码 primitives。"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


class HttpWireBudgetExceeded(ValueError):
    """响应 wire body 超过冻结预算。"""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class HttpResponseContractError(ValueError):
    """响应 transport metadata 不满足解析前合同。"""


class HttpDecodedBudgetViolation(Exception):
    """响应解码后的资源使用超过冻结预算。"""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class HttpContentEncodingFailure(Exception):
    """响应声明的压缩编码无法安全、完整地解码。"""


async def read_bounded_wire_body(
    response: httpx.Response,
    *,
    max_chunk_bytes: int,
    max_wire_bytes: int,
    cumulative_wire_bytes: int,
) -> tuple[bytes, int]:
    """流式读取响应，并在分配完整 body 前执行 metadata/chunk/wire 限制。"""

    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HttpResponseContractError("invalid Content-Length") from exc
        if declared_length < 0:
            raise HttpResponseContractError("negative Content-Length")
        if cumulative_wire_bytes + declared_length > max_wire_bytes:
            raise HttpWireBudgetExceeded("WMS_WIRE_BUDGET_EXCEEDED", "WMS HTTP wire budget exceeded")

    body = bytearray()
    chunks = (response.content,) if response.is_stream_consumed else response.aiter_raw()
    async for chunk in _as_async_chunks(chunks):
        if len(chunk) > max_chunk_bytes:
            raise HttpWireBudgetExceeded("WMS_CHUNK_BUDGET_EXCEEDED", "WMS HTTP chunk budget exceeded")
        cumulative_wire_bytes += len(chunk)
        if cumulative_wire_bytes > max_wire_bytes:
            raise HttpWireBudgetExceeded("WMS_WIRE_BUDGET_EXCEEDED", "WMS HTTP wire budget exceeded")
        body.extend(chunk)
    return bytes(body), cumulative_wire_bytes


async def _as_async_chunks(chunks):
    if hasattr(chunks, "__aiter__"):
        async for chunk in chunks:
            yield chunk
        return
    for chunk in chunks:
        yield chunk


def decode_bounded_http_body(
    raw_body: bytes,
    *,
    content_encoding: str,
    allowed_content_encodings: tuple[str, ...],
    max_decoded_bytes: int,
    max_compression_ratio: float,
) -> bytes:
    """按编码、decoded bytes 与压缩比预算解码一个已完成 wire 限制的 body。"""

    encoding = content_encoding.strip().lower() or "identity"
    if encoding not in allowed_content_encodings:
        raise HttpDecodedBudgetViolation(
            "WMS_UNSUPPORTED_CONTENT_ENCODING",
            f"unsupported WMS content encoding: {encoding}",
        )
    if max_decoded_bytes <= 0:
        raise HttpDecodedBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
    if encoding == "identity":
        decoded = raw_body
    else:
        window_bits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        decoder = zlib.decompressobj(window_bits)
        try:
            decoded = decoder.decompress(raw_body, max_decoded_bytes + 1)
            if decoder.unconsumed_tail or len(decoded) > max_decoded_bytes:
                raise HttpDecodedBudgetViolation(
                    "WMS_DECODED_BUDGET_EXCEEDED",
                    "WMS QUERY decoded budget exceeded",
                )
            decoded += decoder.flush(max_decoded_bytes - len(decoded) + 1)
        except zlib.error as exc:
            raise HttpContentEncodingFailure from exc
        if not decoder.eof or decoder.unused_data:
            raise HttpContentEncodingFailure
    if len(decoded) > max_decoded_bytes:
        raise HttpDecodedBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
    if raw_body and len(decoded) / len(raw_body) > max_compression_ratio:
        raise HttpDecodedBudgetViolation(
            "WMS_COMPRESSION_RATIO_EXCEEDED",
            "WMS QUERY compression ratio budget exceeded",
        )
    return decoded


__all__ = [
    "HttpContentEncodingFailure",
    "HttpDecodedBudgetViolation",
    "HttpResponseContractError",
    "HttpWireBudgetExceeded",
    "decode_bounded_http_body",
    "read_bounded_wire_body",
]
