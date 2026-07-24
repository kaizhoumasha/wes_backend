"""WMS transport endpoint 的部署配置校验。"""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit


def validate_wms_base_url(base_url: str) -> SplitResult:
    """只接受不携带凭据或动态参数的绝对 HTTP(S) 服务根地址。"""

    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in base_url):
        raise ValueError("WMS base URL must be an absolute HTTP URL")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("WMS base URL must be an absolute HTTP URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname or parsed.netloc.endswith(":"):
        raise ValueError("WMS base URL must be an absolute HTTP URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("WMS base URL must not contain userinfo")
    if "?" in base_url:
        raise ValueError("WMS base URL must not contain a query")
    if "#" in base_url:
        raise ValueError("WMS base URL must not contain a fragment")
    return parsed


__all__ = ["validate_wms_base_url"]
