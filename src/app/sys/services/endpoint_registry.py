"""外部 HTTP endpoint 注册表。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class EndpointDefinition:
    """允许 SystemOutbox 派发的 HTTP endpoint。"""

    code: str
    url: str


class EndpointRegistry:
    """解析逻辑 endpoint code，拒绝未注册目标和裸 URL。"""

    def __init__(self, endpoints: Mapping[str, str] | None = None) -> None:
        self._endpoints = dict(endpoints or _load_env_endpoints())

    def resolve(self, target_code: str) -> EndpointDefinition:
        code = _required_target_code(target_code)
        if _looks_like_url(code):
            raise ValueError("SystemOutbox EXTERNAL_HTTP target_code must be a registered endpoint code, not raw URL")
        url = self._endpoints.get(code)
        if not url:
            raise ValueError(f"SystemOutbox endpoint is not registered: {code}")
        return EndpointDefinition(code=code, url=url)


def _load_env_endpoints() -> dict[str, str]:
    endpoints = {
        "WMS_RCS_RACK_OPERATION": os.getenv("WMS_RCS_RACK_OPERATION_URL", "http://wms-rcs/api/wes/rack-operation"),
        "WMS_RCS_BIN_OPERATION": os.getenv("WMS_RCS_BIN_OPERATION_URL", "http://wms-rcs/api/wes/transport-request"),
        "WMS_RCS_FULL_BOX_EXCHANGE": os.getenv(
            "WMS_RCS_FULL_BOX_EXCHANGE_URL",
            os.getenv("WMS_RCS_BIN_OPERATION_URL", "http://wms-rcs/api/wes/transport-request"),
        ),
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

__all__ = ["EndpointDefinition", "EndpointRegistry", "endpoint_registry"]
