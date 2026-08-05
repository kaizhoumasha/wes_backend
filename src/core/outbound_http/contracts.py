"""框架无关的出站 HTTP 传输合同。"""

from __future__ import annotations

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
_SUPPORTED_CONTENT_ENCODINGS = frozenset({"identity", "gzip", "deflate"})
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


def _as_string_pairs(
    pairs: tuple[tuple[str, str], ...],
    *,
    field_name: str,
    reject_duplicates: bool,
) -> tuple[tuple[str, str], ...]:
    normalized_pairs = tuple(pairs)
    names: set[str] = set()
    for name, value in normalized_pairs:
        if not isinstance(name, str) or not isinstance(value, str):
            raise OutboundHttpRequestError(f"{field_name} must contain string pairs")
        if not name or _contains_control_character(name) or _contains_control_character(value):
            raise OutboundHttpRequestError(f"{field_name} contains an invalid header")
        normalized_name = name.casefold()
        if reject_duplicates and normalized_name in names:
            raise OutboundHttpRequestError(f"{field_name} contains a duplicate header")
        names.add(normalized_name)
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
    allowed_content_encodings: tuple[str, ...] = ("identity", "gzip", "deflate")

    def __post_init__(self) -> None:
        numeric_limits = (
            self.max_response_header_count,
            self.max_response_header_wire_bytes,
            self.max_chunk_bytes,
            self.max_wire_bytes,
            self.max_decoded_bytes,
            self.max_compression_ratio,
        )
        if any(value <= 0 for value in numeric_limits):
            raise OutboundHttpRequestError("response limit must be positive")
        if self.max_response_header_count > _MAX_RESPONSE_HEADER_COUNT:
            raise OutboundHttpRequestError("response limit exceeds header count cap")
        if self.max_response_header_wire_bytes > _MAX_RESPONSE_HEADER_WIRE_BYTES:
            raise OutboundHttpRequestError("response limit exceeds header wire cap")

        encodings = tuple(self.allowed_content_encodings)
        if (
            not encodings
            or "identity" not in encodings
            or len(set(encodings)) != len(encodings)
            or any(encoding not in _SUPPORTED_CONTENT_ENCODINGS for encoding in encodings)
        ):
            raise OutboundHttpRequestError("response limit contains unsupported content encoding")
        object.__setattr__(self, "allowed_content_encodings", encodings)


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

        query = tuple(self.query)
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

        object.__setattr__(self, "query", query)
        object.__setattr__(self, "headers", _as_string_pairs(self.headers, field_name="header", reject_duplicates=True))
        object.__setattr__(self, "body", bytes(self.body))


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

        headers = _as_string_pairs(self.response_headers, field_name="response header", reject_duplicates=False)
        if self.decoded_body is not None:
            object.__setattr__(self, "decoded_body", bytes(self.decoded_body))
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

    async def aclose(self) -> None:
        """释放 Transport 持有的 HTTP 资源。"""


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
