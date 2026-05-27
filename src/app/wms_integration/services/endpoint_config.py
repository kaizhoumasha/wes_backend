"""WMS 同步端口 endpoint 配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

import httpx

if TYPE_CHECKING:
    from src.app.wms_integration.models import WmsOperationName

DEFAULT_WMS_SYNC_BASE_URL = "http://wms/api"
WmsHttpMethod = Literal["DELETE", "GET", "POST"]


@dataclass(frozen=True)
class WmsHttpTimeoutConfig:
    """WMS HTTP 显式 timeout 配置。"""

    connect: float = 3.0
    read: float = 10.0
    write: float = 5.0
    pool: float = 3.0

    def to_httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(connect=self.connect, read=self.read, write=self.write, pool=self.pool)


@dataclass(frozen=True)
class WmsOperationEndpoint:
    """单个 WMS 同步操作 endpoint。"""

    operation_name: WmsOperationName
    http_method: WmsHttpMethod
    target_code: str
    base_url: str
    path: str
    timeout: WmsHttpTimeoutConfig

    @property
    def url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.path.lstrip('/')}"


class WmsEndpointConfig:
    """解析 WMS 同步端口配置。

    该配置只服务同步 WMS typed ports；`sys.EndpointRegistry` 仍只负责 SystemOutbox EXTERNAL_HTTP。
    """

    _DEFAULT_PATHS: ClassVar[dict[WmsOperationName, tuple[str, str, str, WmsHttpMethod]]] = {
        "query_inventory": (
            "WMS_INVENTORY",
            "WMS_SYNC_QUERY_INVENTORY_PATH",
            "/inventory/query",
            "GET",
        ),
        "reserve_inventory": (
            "WMS_INVENTORY",
            "WMS_SYNC_RESERVE_INVENTORY_PATH",
            "/inventory/reserve",
            "POST",
        ),
        "release_reservation": (
            "WMS_INVENTORY",
            "WMS_SYNC_RELEASE_RESERVATION_PATH",
            "/inventory/reserve/{id}",
            "DELETE",
        ),
        "confirm_inbound": (
            "WMS_INBOUND",
            "WMS_SYNC_CONFIRM_INBOUND_PATH",
            "/inbound/confirm",
            "POST",
        ),
        "confirm_outbound": (
            "WMS_OUTBOUND",
            "WMS_SYNC_CONFIRM_OUTBOUND_PATH",
            "/outbound/confirm",
            "POST",
        ),
    }

    def __init__(
        self,
        *,
        base_url: str | None = None,
        paths: dict[WmsOperationName, str] | None = None,
        target_codes: dict[WmsOperationName, str] | None = None,
        http_methods: dict[WmsOperationName, WmsHttpMethod] | None = None,
        timeout: WmsHttpTimeoutConfig | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("WMS_SYNC_BASE_URL") or DEFAULT_WMS_SYNC_BASE_URL).rstrip("/")
        self.paths = paths or {}
        self.target_codes = target_codes or {}
        self.http_methods = http_methods or {}
        self.timeout = timeout or WmsHttpTimeoutConfig(
            connect=_env_float("WMS_SYNC_CONNECT_TIMEOUT_SECONDS", 3.0),
            read=_env_float("WMS_SYNC_READ_TIMEOUT_SECONDS", 10.0),
            write=_env_float("WMS_SYNC_WRITE_TIMEOUT_SECONDS", 5.0),
            pool=_env_float("WMS_SYNC_POOL_TIMEOUT_SECONDS", 3.0),
        )

    def resolve(self, operation_name: WmsOperationName) -> WmsOperationEndpoint:
        target_code, path_env, default_path, default_http_method = self._DEFAULT_PATHS[operation_name]
        return WmsOperationEndpoint(
            operation_name=operation_name,
            http_method=self.http_methods.get(operation_name, default_http_method),
            target_code=self.target_codes.get(operation_name, target_code),
            base_url=self.base_url,
            path=self.paths.get(operation_name) or os.getenv(path_env) or default_path,
            timeout=self.timeout,
        )


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


wms_endpoint_config = WmsEndpointConfig()


__all__ = [
    "DEFAULT_WMS_SYNC_BASE_URL",
    "WmsEndpointConfig",
    "WmsHttpMethod",
    "WmsHttpTimeoutConfig",
    "WmsOperationEndpoint",
    "wms_endpoint_config",
]
