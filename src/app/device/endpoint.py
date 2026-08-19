"""Device 静态 Endpoint 的唯一校验与规范化入口。"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_INTERNAL_SERVICE_NAME = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
_DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DNS_HOSTNAME = re.compile(rf"{_DNS_LABEL}(?:\.{_DNS_LABEL})+")
_LEGACY_NUMERIC_HOST = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*")
_LAN_NETWORKS = tuple(
    ipaddress.ip_network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def validate_device_endpoint_base_url(value: str) -> str:
    """返回 canonical 局域网 HTTP origin；其余输入 fail closed。"""

    if (
        not value
        or value != value.strip()
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError("Device Endpoint 必须是非空且无首尾空白的局域网 http origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("Device Endpoint 必须是无凭据、路径、query 和 fragment 的局域网 http origin")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Device Endpoint 端口必须是 1..65535 的整数") from error
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Device Endpoint 端口必须是 1..65535 的整数")

    hostname = parsed.hostname
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        canonical_host = hostname.lower()
        if (
            _INTERNAL_SERVICE_NAME.fullmatch(canonical_host) is None
            and (_DNS_HOSTNAME.fullmatch(canonical_host) is None or len(canonical_host) > 253)
        ) or _LEGACY_NUMERIC_HOST.fullmatch(canonical_host) is not None:
            raise ValueError("Device Endpoint 主机必须是局域网 IP、内部单段服务名或完整域名") from None
    else:
        if not address.is_loopback and not any(address in network for network in _LAN_NETWORKS):
            raise ValueError("Device Endpoint 必须指向局域网地址")
        canonical_host = f"[{address.compressed}]" if address.version == 6 else address.compressed

    canonical_port = "" if port in {None, 80} else f":{port}"
    return f"http://{canonical_host}{canonical_port}"


__all__ = ["validate_device_endpoint_base_url"]
