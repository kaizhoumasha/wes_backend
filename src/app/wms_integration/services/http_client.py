"""WMS 同步 HTTP client。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from src.app.wms_integration.services.exceptions import (
    WmsTimeoutError,
    WmsUnavailableError,
)

if TYPE_CHECKING:
    from src.app.wms_integration.services.endpoint_config import WmsOperationEndpoint


@dataclass(frozen=True)
class WmsHttpResult:
    """WMS HTTP 响应结果。"""

    status_code: int
    payload: Any


class WmsHttpClient:
    """基于 httpx 的 WMS HTTP client。"""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def post_json(self, endpoint: WmsOperationEndpoint, payload: dict[str, Any]) -> WmsHttpResult:
        """向配置化 endpoint 发送 JSON 请求。业务域不传裸 URL。"""

        return await self._request_json(endpoint, method="POST", json=payload)

    async def get_json(self, endpoint: WmsOperationEndpoint, params: dict[str, Any]) -> WmsHttpResult:
        """向配置化 endpoint 发送 GET 查询请求。业务域不传裸 URL。"""

        return await self._request_json(endpoint, method="GET", params=params)

    async def delete(self, endpoint: WmsOperationEndpoint) -> WmsHttpResult:
        """向配置化 endpoint 发送 DELETE 请求。业务域不传裸 URL。"""

        return await self._request_json(endpoint, method="DELETE")

    async def _request_json(
        self,
        endpoint: WmsOperationEndpoint,
        *,
        method: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> WmsHttpResult:
        try:
            async with httpx.AsyncClient(
                timeout=endpoint.timeout.to_httpx_timeout(),
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.request(method, endpoint.url, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise WmsTimeoutError(
                "WMS 同步调用超时",
                operation_name=endpoint.operation_name,
                evidence_key=None,
                reason_code="WMS_TIMEOUT",
                retryable=True,
                target_code=endpoint.target_code,
            ) from exc
        except httpx.RequestError as exc:
            raise WmsUnavailableError(
                "WMS 同步调用网络异常",
                operation_name=endpoint.operation_name,
                evidence_key=None,
                reason_code="WMS_NETWORK_ERROR",
                retryable=True,
                target_code=endpoint.target_code,
            ) from exc

        try:
            response_payload: Any = response.json()
        except ValueError:
            response_payload = response.text
        return WmsHttpResult(status_code=response.status_code, payload=response_payload)


wms_http_client = WmsHttpClient()


__all__ = [
    "WmsHttpClient",
    "WmsHttpResult",
    "wms_http_client",
]
