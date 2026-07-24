"""外部 HTTP endpoint 注册表。"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from src.app.sys.canonical_dispatch import EndpointDefinition
from src.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import Mapping


ENDPOINT_SETTING_BY_TARGET_CODE = MappingProxyType(
    {
        "WMS_RCS_RACK_OPERATION": "WMS_RCS_RACK_OPERATION_URL",
        "WMS_RCS_BIN_OPERATION": "WMS_RCS_BIN_OPERATION_URL",
        "WMS_RCS_FULL_BOX_EXCHANGE": "WMS_RCS_FULL_BOX_EXCHANGE_URL",
    }
)


class EndpointRegistry:
    """解析逻辑 endpoint code，拒绝未注册目标和裸 URL。"""

    def __init__(
        self,
        endpoints: Mapping[str, str] | None = None,
        *,
        settings_source: Any | None = None,
    ) -> None:
        self._endpoints = dict(
            endpoints
            if endpoints is not None
            else _load_env_endpoints(settings if settings_source is None else settings_source)
        )

    def resolve(self, target_code: str) -> EndpointDefinition:
        code = _required_target_code(target_code)
        if _looks_like_url(code):
            raise ValueError("SystemOutbox EXTERNAL_HTTP target_code must be a registered endpoint code, not raw URL")
        url = self._endpoints.get(code)
        if not url:
            raise ValueError(f"SystemOutbox endpoint is not registered: {code}")
        return EndpointDefinition(code=code, url=url)


def _load_env_endpoints(settings_source: Any) -> dict[str, str]:
    endpoints = {
        target_code: getattr(settings_source, setting_name)
        for target_code, setting_name in ENDPOINT_SETTING_BY_TARGET_CODE.items()
    }
    return {key: value for key, value in endpoints.items() if value and value.strip()}


def _required_target_code(value: str) -> str:
    code = str(value or "").strip()
    if not code:
        raise ValueError("SystemOutbox target_code is required")
    return code


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


endpoint_registry = EndpointRegistry()

__all__ = ["ENDPOINT_SETTING_BY_TARGET_CODE", "EndpointDefinition", "EndpointRegistry", "endpoint_registry"]
