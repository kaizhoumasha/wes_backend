"""框架无关的出站 HTTP 传输合同。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from urllib.parse import urlsplit


class OutboundHttpMethod(str, Enum):
    """当前基础层允许的 HTTP 方法。"""

    GET = "GET"
    POST = "POST"


class OutboundHttpDeliveryState(str, Enum):
    """发送生命周期中可确认的传输事实。"""

    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"


class OutboundHttpFailureKind(str, Enum):
    """稳定且不携带业务语义的传输失败分类。"""

    POOL_TIMEOUT = "POOL_TIMEOUT"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    CONNECT_ERROR = "CONNECT_ERROR"
    WRITE_TIMEOUT = "WRITE_TIMEOUT"
    WRITE_ERROR = "WRITE_ERROR"
    READ_TIMEOUT = "READ_TIMEOUT"
    READ_ERROR = "READ_ERROR"
    REMOTE_PROTOCOL_ERROR = "REMOTE_PROTOCOL_ERROR"
    TOTAL_TIMEOUT = "TOTAL_TIMEOUT"
    RESPONSE_HEADER_LIMIT_EXCEEDED = "RESPONSE_HEADER_LIMIT_EXCEEDED"
    RESPONSE_METADATA_INVALID = "RESPONSE_METADATA_INVALID"
    RESPONSE_CHUNK_LIMIT_EXCEEDED = "RESPONSE_CHUNK_LIMIT_EXCEEDED"
    RESPONSE_WIRE_LIMIT_EXCEEDED = "RESPONSE_WIRE_LIMIT_EXCEEDED"
    RESPONSE_DECODED_LIMIT_EXCEEDED = "RESPONSE_DECODED_LIMIT_EXCEEDED"
    RESPONSE_COMPRESSION_RATIO_EXCEEDED = "RESPONSE_COMPRESSION_RATIO_EXCEEDED"
    RESPONSE_CONTENT_ENCODING_UNSUPPORTED = "RESPONSE_CONTENT_ENCODING_UNSUPPORTED"
    RESPONSE_CONTENT_ENCODING_INVALID = "RESPONSE_CONTENT_ENCODING_INVALID"
    RESPONSE_CLEANUP_FAILED = "RESPONSE_CLEANUP_FAILED"


class OutboundHttpRequestError(ValueError):
    """请求值违反出站 HTTP 合同时抛出。"""


class OutboundHttpClosedError(RuntimeError):
    """Transport 已关闭后仍尝试发送时抛出。"""


_MAX_RESPONSE_HEADER_COUNT = 64
_MAX_RESPONSE_HEADER_WIRE_BYTES = 16_384
_HTTP_TOKEN_CHARACTERS = frozenset("!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_NOT_SENT_FAILURES = frozenset(
    {
        OutboundHttpFailureKind.POOL_TIMEOUT,
        OutboundHttpFailureKind.CONNECT_TIMEOUT,
        OutboundHttpFailureKind.CONNECT_ERROR,
    }
)
_DELIVERY_UNKNOWN_FAILURES = frozenset(
    {
        OutboundHttpFailureKind.WRITE_TIMEOUT,
        OutboundHttpFailureKind.WRITE_ERROR,
        OutboundHttpFailureKind.READ_TIMEOUT,
        OutboundHttpFailureKind.READ_ERROR,
        OutboundHttpFailureKind.REMOTE_PROTOCOL_ERROR,
        OutboundHttpFailureKind.TOTAL_TIMEOUT,
    }
)
_RESPONSE_FAILURES = frozenset(
    {
        OutboundHttpFailureKind.READ_TIMEOUT,
        OutboundHttpFailureKind.READ_ERROR,
        OutboundHttpFailureKind.REMOTE_PROTOCOL_ERROR,
        OutboundHttpFailureKind.TOTAL_TIMEOUT,
        OutboundHttpFailureKind.RESPONSE_HEADER_LIMIT_EXCEEDED,
        OutboundHttpFailureKind.RESPONSE_METADATA_INVALID,
        OutboundHttpFailureKind.RESPONSE_CHUNK_LIMIT_EXCEEDED,
        OutboundHttpFailureKind.RESPONSE_WIRE_LIMIT_EXCEEDED,
        OutboundHttpFailureKind.RESPONSE_DECODED_LIMIT_EXCEEDED,
        OutboundHttpFailureKind.RESPONSE_COMPRESSION_RATIO_EXCEEDED,
        OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_UNSUPPORTED,
        OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_INVALID,
        OutboundHttpFailureKind.RESPONSE_CLEANUP_FAILED,
    }
)


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_valid_header_name(name: str) -> bool:
    return bool(name) and all(character in _HTTP_TOKEN_CHARACTERS for character in name)


def _as_string_pairs(
    pairs: tuple[tuple[str, str], ...],
    *,
    field_name: str,
    reject_duplicates: bool,
) -> tuple[tuple[str, str], ...]:
    normalized_pairs = _as_string_pair_tuple(pairs, field_name=field_name)
    names: set[str] = set()
    for name, value in normalized_pairs:
        if not _is_valid_header_name(name) or _contains_control_character(value):
            raise OutboundHttpRequestError(f"{field_name} contains an invalid header")
        normalized_name = name.casefold()
        if reject_duplicates and normalized_name in names:
            raise OutboundHttpRequestError(f"{field_name} contains a duplicate header")
        names.add(normalized_name)
    return normalized_pairs


def _is_valid_response_header(name: str, value: str) -> bool:
    if not _is_valid_header_name(name):
        return False
    return not any((ord(character) < 32 and character != "\t") or ord(character) == 127 for character in value)


def _as_response_header_pairs(pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    normalized_pairs = _as_string_pair_tuple(pairs, field_name="response header")
    if any(not _is_valid_response_header(name, value) for name, value in normalized_pairs):
        raise OutboundHttpRequestError("response header contains an invalid header")
    return normalized_pairs


def _as_string_pair_tuple(
    pairs: tuple[tuple[str, str], ...],
    *,
    field_name: str,
) -> tuple[tuple[str, str], ...]:
    normalized_pairs = tuple(pairs)
    for pair in normalized_pairs:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not isinstance(pair[1], str)
        ):
            raise OutboundHttpRequestError(f"{field_name} must contain string pairs")
    return normalized_pairs


@dataclass(frozen=True, slots=True)
class OutboundHttpResponseLimits:
    """每次响应读取可用的固定资源预算。"""

    max_response_header_count: int = _MAX_RESPONSE_HEADER_COUNT
    max_response_header_wire_bytes: int = _MAX_RESPONSE_HEADER_WIRE_BYTES
    max_chunk_bytes: int = 64 * 1024
    max_wire_bytes: int = 2 * 1024 * 1024
    max_decoded_bytes: int = 4 * 1024 * 1024
    max_compression_ratio: float = 100.0

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_response_header_count,
            self.max_response_header_wire_bytes,
            self.max_chunk_bytes,
            self.max_wire_bytes,
            self.max_decoded_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in integer_limits):
            raise OutboundHttpRequestError("response limit must be a positive integer")
        if (
            isinstance(self.max_compression_ratio, bool)
            or not isinstance(self.max_compression_ratio, (int, float))
            or not math.isfinite(self.max_compression_ratio)
            or self.max_compression_ratio <= 0
        ):
            raise OutboundHttpRequestError("response limit compression ratio must be a finite positive number")
        if self.max_response_header_count > _MAX_RESPONSE_HEADER_COUNT:
            raise OutboundHttpRequestError("response limit exceeds header count cap")
        if self.max_response_header_wire_bytes > _MAX_RESPONSE_HEADER_WIRE_BYTES:
            raise OutboundHttpRequestError("response limit exceeds header wire cap")


@dataclass(frozen=True, slots=True)
class OutboundHttpRequest:
    """一次框架无关的 HTTP 请求。"""

    method: OutboundHttpMethod
    path: str
    query: tuple[tuple[str, str], ...] = field(default_factory=tuple, repr=False)
    headers: tuple[tuple[str, str], ...] = field(default_factory=tuple, repr=False)
    body: bytes = field(default=b"", repr=False)
    response_limits: OutboundHttpResponseLimits = field(default_factory=OutboundHttpResponseLimits)

    def __post_init__(self) -> None:
        if not isinstance(self.method, OutboundHttpMethod):
            raise TypeError("method must be an OutboundHttpMethod")
        if not isinstance(self.path, str) or _contains_control_character(self.path):
            raise OutboundHttpRequestError("path must not contain control characters")
        parsed_path = urlsplit(self.path)
        if (
            not self.path.startswith("/")
            or self.path.startswith("//")
            or parsed_path.scheme
            or parsed_path.netloc
            or parsed_path.query
            or parsed_path.fragment
        ):
            raise OutboundHttpRequestError("path must be an unambiguous relative path")

        query = _as_string_pair_tuple(self.query, field_name="query")
        for key, value in query:
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or _contains_control_character(key)
                or _contains_control_character(value)
            ):
                raise OutboundHttpRequestError("query contains invalid value")

        if not isinstance(self.response_limits, OutboundHttpResponseLimits):
            raise TypeError("response limit must be an OutboundHttpResponseLimits")
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")

        headers = _as_string_pairs(self.headers, field_name="header", reject_duplicates=True)
        if any(name.casefold() == "accept-encoding" for name, _ in headers):
            raise OutboundHttpRequestError("Accept-Encoding is owned by the transport")

        object.__setattr__(self, "query", query)
        object.__setattr__(self, "headers", headers)


@dataclass(frozen=True, slots=True)
class OutboundHttpResult:
    """不掺入业务结论的出站 HTTP 传输结果。"""

    delivery_state: OutboundHttpDeliveryState
    status_code: int | None = None
    response_headers: tuple[tuple[str, str], ...] = field(default_factory=tuple, repr=False)
    decoded_body: bytes | None = field(default=None, repr=False)
    failure_kind: OutboundHttpFailureKind | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.delivery_state, OutboundHttpDeliveryState):
            raise TypeError("result delivery state is invalid")
        if self.status_code is not None and not isinstance(self.status_code, int):
            raise TypeError("result status code is invalid")
        if self.status_code is not None and not 100 <= self.status_code <= 599:
            raise ValueError("result status code is invalid")
        if self.failure_kind is not None and not isinstance(self.failure_kind, OutboundHttpFailureKind):
            raise TypeError("result failure kind is invalid")
        if self.decoded_body is not None and not isinstance(self.decoded_body, bytes):
            raise TypeError("result decoded body is invalid")

        headers = _as_response_header_pairs(self.response_headers)
        object.__setattr__(self, "response_headers", headers)

        if self.delivery_state is OutboundHttpDeliveryState.NOT_SENT:
            is_valid = (
                self.status_code is None
                and not headers
                and self.decoded_body is None
                and self.failure_kind in _NOT_SENT_FAILURES
            )
        elif self.delivery_state is OutboundHttpDeliveryState.DELIVERY_UNKNOWN:
            is_valid = (
                self.status_code is None
                and not headers
                and self.decoded_body is None
                and self.failure_kind in _DELIVERY_UNKNOWN_FAILURES
            )
        elif self.failure_kind is None:
            is_valid = self.status_code is not None and self.decoded_body is not None
        else:
            is_valid = (
                self.status_code is not None
                and self.decoded_body is None
                and self.failure_kind in _RESPONSE_FAILURES
                and (self.failure_kind is not OutboundHttpFailureKind.RESPONSE_HEADER_LIMIT_EXCEEDED or not headers)
            )
        if not is_valid:
            raise ValueError("result does not match the delivery-state contract")


class OutboundHttpTransport(Protocol):
    """后续 Adapter 消费的最小异步传输端口。"""

    async def send(self, request: OutboundHttpRequest) -> OutboundHttpResult:
        """发送一次请求并返回传输事实。"""

        ...

    async def aclose(self) -> None:
        """释放 Transport 持有的 HTTP 资源。"""

        ...


__all__ = [
    "OutboundHttpClosedError",
    "OutboundHttpDeliveryState",
    "OutboundHttpFailureKind",
    "OutboundHttpMethod",
    "OutboundHttpRequest",
    "OutboundHttpRequestError",
    "OutboundHttpResponseLimits",
    "OutboundHttpResult",
    "OutboundHttpTransport",
]
