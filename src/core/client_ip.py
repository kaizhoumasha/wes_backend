from __future__ import annotations

from contextlib import suppress
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import TYPE_CHECKING

from src.core.conf import settings

if TYPE_CHECKING:
    from fastapi import Request

TrustedNetwork = IPv4Network | IPv6Network

UNKNOWN_CLIENT_IP = "unknown"
TESTCLIENT_HOST = "testclient"
TESTCLIENT_IP = "127.0.0.1"


def _normalize_host(value: str | None) -> str:
    if not value:
        return UNKNOWN_CLIENT_IP
    host = value.strip()
    if host == TESTCLIENT_HOST:
        return TESTCLIENT_IP
    return host or UNKNOWN_CLIENT_IP


def _parse_ip(value: str | None):
    host = _normalize_host(value)
    if host == UNKNOWN_CLIENT_IP:
        return None
    try:
        return ip_address(host)
    except ValueError:
        return None


def _trusted_proxy_networks() -> list[TrustedNetwork]:
    networks: list[TrustedNetwork] = []
    for item in settings.TRUSTED_PROXY_IPS:
        value = item.strip()
        if not value:
            continue
        with suppress(ValueError):
            networks.append(ip_network(value, strict=False))
    return networks


def _is_trusted_proxy(peer_host: str, trusted_networks: list[TrustedNetwork]) -> bool:
    peer_ip = _parse_ip(peer_host)
    return peer_ip is not None and any(peer_ip in network for network in trusted_networks)


def _first_untrusted_forwarded_ip(header_value: str, trusted_networks: list[TrustedNetwork]) -> str | None:
    raw_items = [item.strip() for item in header_value.split(",") if item.strip()]
    for item in reversed(raw_items):
        parsed_ip = _parse_ip(item)
        if parsed_ip is None:
            continue
        if not any(parsed_ip in network for network in trusted_networks):
            return str(parsed_ip)
    return None


def resolve_client_ip(request: Request) -> str:
    """Resolve client IP from a request, trusting proxy headers only from configured proxies."""

    peer_host = _normalize_host(request.client.host if request.client else None)
    trusted_networks = _trusted_proxy_networks()
    if not _is_trusted_proxy(peer_host, trusted_networks):
        return peer_host

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        forwarded_ip = _first_untrusted_forwarded_ip(forwarded_for, trusted_networks)
        return forwarded_ip or peer_host

    real_ip = _parse_ip(request.headers.get("X-Real-IP"))
    return str(real_ip) if real_ip is not None else peer_host


__all__ = ["resolve_client_ip"]
