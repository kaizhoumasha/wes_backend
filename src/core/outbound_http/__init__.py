"""出站 HTTP 传输基础能力。"""

from .contracts import (
    OutboundHttpClosedError,
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpRequestError,
    OutboundHttpResponseLimits,
    OutboundHttpResult,
    OutboundHttpTransport,
)
from .factory import build_outbound_http_transport

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
    "build_outbound_http_transport",
]
