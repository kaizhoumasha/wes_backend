"""外部 HTTP endpoint 注册表。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from src.app.sys.canonical_dispatch import EndpointDefinition

if TYPE_CHECKING:
    from collections.abc import Mapping


class EndpointRegistry:
    """解析逻辑 endpoint code，拒绝未注册目标和裸 URL。"""

    def __init__(
        self,
        endpoints: Mapping[str, str] | None = None,
        *,
        settings_source: Any | None = None,
    ) -> None:
        # 旧 WMS/RCS settings catalog 已删除；运行时只能显式注入冻结的 typed operation endpoint。
        del settings_source
        self._endpoints = dict(endpoints or {})

    def resolve(self, target_code: str) -> EndpointDefinition:
        code = _required_target_code(target_code)
        if _looks_like_url(code):
            raise ValueError("SystemOutbox EXTERNAL_HTTP target_code must be a registered endpoint code, not raw URL")
        url = self._endpoints.get(code)
        if not url:
            raise ValueError(f"SystemOutbox endpoint is not registered: {code}")
        return EndpointDefinition(code=code, url=url)


def _required_target_code(value: str) -> str:
    code = str(value or "").strip()
    if not code:
        raise ValueError("SystemOutbox target_code is required")
    return code


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


endpoint_registry = EndpointRegistry()

__all__ = ["EndpointDefinition", "EndpointRegistry", "endpoint_registry"]
