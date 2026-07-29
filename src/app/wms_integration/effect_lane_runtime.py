"""WMS EFFECT actual lane 的长期 HTTP client owner。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.app.wms_integration.operation_registry import EFFECT_OPERATION_IDENTITIES
from src.app.wms_integration.provider_readiness import WmsProviderProcessRole

if TYPE_CHECKING:
    import httpx

    from src.app.sys.canonical_dispatch import ExternalHttpDispatchRequest
    from src.app.sys.external_http_transport import ExternalHttpTransportResult
    from src.app.wms_integration.provider_readiness import WmsProviderReadiness
    from src.app.wms_integration.provider_startup import WmsProviderStartupConfiguration


class WmsEffectLaneRuntime:
    """一个 worker child/事件循环只为其 actual EFFECT lane 持有一个 client。"""

    def __init__(
        self,
        *,
        process_role: WmsProviderProcessRole,
        readiness: WmsProviderReadiness,
        client: httpx.AsyncClient,
    ) -> None:
        if readiness.process_role is not process_role:
            raise ValueError("WMS EFFECT lane runtime process role/readiness mismatch")
        lane_effect_identities = frozenset(readiness.operation_identities) & EFFECT_OPERATION_IDENTITIES
        if not lane_effect_identities:
            raise ValueError("WMS EFFECT lane readiness must include at least one EFFECT operation")
        self._process_role = process_role
        self._readiness = readiness
        self._client = client
        self._operation_identities = lane_effect_identities

    @property
    def process_role(self) -> WmsProviderProcessRole:
        return self._process_role

    @property
    def readiness(self) -> WmsProviderReadiness:
        return self._readiness

    @property
    def client(self) -> httpx.AsyncClient:
        """供同一 actual lane 的 data QUERY / fulfillment STATUS runtime 借用，不转移关闭责任。"""

        return self._client

    @property
    def operation_identities(self) -> frozenset[str]:
        return self._operation_identities

    async def send(self, request: ExternalHttpDispatchRequest) -> ExternalHttpTransportResult:
        if request.operation_identity not in self._operation_identities:
            raise ValueError("WMS EFFECT request does not belong to the worker actual lane")
        from src.app.sys.services.outbox_engine import send_external_http_with_client

        return await send_external_http_with_client(request, client=self._client)

    async def aclose(self) -> None:
        await self._client.aclose()


_active_runtime: WmsEffectLaneRuntime | None = None
_active_loop: asyncio.AbstractEventLoop | None = None


def bind_wms_effect_lane_runtime(runtime: WmsEffectLaneRuntime) -> None:
    """发布当前 worker child/owner loop 唯一的 actual EFFECT lane runtime。"""

    global _active_loop, _active_runtime
    loop = asyncio.get_running_loop()
    if _active_runtime is not None and (_active_runtime is not runtime or _active_loop is not loop):
        raise RuntimeError("WMS EFFECT lane runtime is already bound")
    _active_runtime = runtime
    _active_loop = loop


def get_wms_effect_lane_runtime() -> WmsEffectLaneRuntime | None:
    if _active_runtime is None:
        return None
    if _active_loop is not asyncio.get_running_loop():
        raise RuntimeError("WMS EFFECT lane runtime event loop mismatch")
    return _active_runtime


def build_wms_effect_lane_runtime(
    startup: WmsProviderStartupConfiguration,
    *,
    process_role: WmsProviderProcessRole,
) -> WmsEffectLaneRuntime:
    """从同一 compiled profile/readiness 构造 actual lane 的唯一长期 client。"""

    import httpx

    readiness = startup.wes_readiness if process_role is WmsProviderProcessRole.WES else startup.fulfillment_readiness
    return WmsEffectLaneRuntime(
        process_role=process_role,
        readiness=readiness,
        client=httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
            trust_env=False,
        ),
    )


async def close_bound_wms_effect_lane_runtime() -> None:
    """关闭并撤销 owner loop 的唯一 actual EFFECT lane runtime。"""

    global _active_loop, _active_runtime
    runtime = get_wms_effect_lane_runtime()
    if runtime is None:
        return
    _active_runtime = None
    _active_loop = None
    try:
        from src.celery_app.outbox_dispatch_composition import clear_scoped_outbox_engine_cache

        clear_scoped_outbox_engine_cache()
    finally:
        await runtime.aclose()


__all__ = [
    "WmsEffectLaneRuntime",
    "bind_wms_effect_lane_runtime",
    "build_wms_effect_lane_runtime",
    "close_bound_wms_effect_lane_runtime",
    "get_wms_effect_lane_runtime",
]
