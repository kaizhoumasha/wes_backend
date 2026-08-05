"""出站 HTTP Transport 的最小构造入口。"""

from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

import httpx

from src.core.outbound_http.contracts import OutboundHttpRequestError, OutboundHttpTransport
from src.core.outbound_http.transport import _HttpxOutboundHttpTransport

_SYSTEM_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def build_outbound_http_transport(
    *,
    system_id: str,
    base_url: str,
    timeout_seconds: float,
) -> OutboundHttpTransport:
    """构造一个供单一外部系统长期持有的 Transport。"""

    _validate_system_id(system_id)
    _validate_base_url(base_url)
    _validate_timeout(timeout_seconds)
    client = httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
        follow_redirects=False,
    )
    return _HttpxOutboundHttpTransport(
        client=client,
        system_id=system_id,
        timeout_seconds=timeout_seconds,
    )


def _validate_system_id(system_id: str) -> None:
    if not isinstance(system_id, str) or _SYSTEM_ID_PATTERN.fullmatch(system_id) is None:
        raise OutboundHttpRequestError("system_id must match the stable identifier contract")


def _validate_base_url(base_url: str) -> None:
    if not isinstance(base_url, str) or any(ord(character) < 32 or ord(character) == 127 for character in base_url):
        raise OutboundHttpRequestError("base URL contains invalid characters")

    try:
        parsed_url = urlsplit(base_url)
        _ = parsed_url.port
    except ValueError as error:
        raise OutboundHttpRequestError("base URL contains an invalid port") from error

    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path not in {"", "/"}
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise OutboundHttpRequestError("base URL must contain only an HTTP origin")


def _validate_timeout(timeout_seconds: float) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise OutboundHttpRequestError("timeout must be a finite positive number")


__all__ = ["build_outbound_http_transport"]
