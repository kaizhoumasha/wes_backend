"""Transport 唯一生产组合根。"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from src.app.sys.services.event_stream_service import event_stream_service
from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_integration.provider_profile import WmsProviderAuthScheme
from src.core.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.contracts import TransportPort
    from src.app.wms_adapter.client import WmsClient
    from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
    from src.app.wms_adapter.transport_event_handler import TransportEventHandler
    from src.app.wms_integration.provider_startup import WmsProviderStartupConfiguration


class TransportRuntime:
    """由一个进程和事件循环独占的 Transport 资源集合。"""

    def __init__(
        self,
        *,
        client: WmsClient,
        repository: TransportRepository,
        adapter: WmsTransportAdapter,
        service: TransportService,
        handler: TransportEventHandler,
    ) -> None:
        self.client = client
        self.repository = repository
        self.adapter = adapter
        self.service = service
        self.port: TransportPort = service
        self.handler = handler
        self._owner_pid = os.getpid()
        self._owner_loop = asyncio.get_running_loop()
        self._close_lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def aclose(self) -> None:
        """在 owner loop 幂等关闭唯一 WMS Client。"""

        current_pid = os.getpid()
        if current_pid != self._owner_pid:
            raise RuntimeError(
                f"TransportRuntime owner PID mismatch (owner={self._owner_pid}, current={current_pid}); "
                "refusing fork-inherited runtime access"
            )
        if asyncio.get_running_loop() is not self._owner_loop:
            raise RuntimeError("TransportRuntime owner event loop mismatch")
        async with self._close_lock:
            if self._closed:
                return
            await self.client.aclose()
            self._closed = True


def validate_transport_runtime_profile(startup: WmsProviderStartupConfiguration) -> None:
    """在资源创建前拒绝 Transport 尚未实现的网络或认证配置。"""

    profile = startup.compiled_profile.profile
    if profile.network_trust_mode != "isolated_lan":
        raise ValueError("Transport runtime requires network_trust_mode=isolated_lan")
    if profile.outbound_auth.scheme is not WmsProviderAuthScheme.NONE:
        raise ValueError("Transport runtime requires outbound_auth.scheme=NONE")
    if profile.inbound_auth.scheme is not WmsProviderAuthScheme.NONE:
        raise ValueError("Transport runtime requires inbound_auth.scheme=NONE")


async def build_transport_runtime(
    *,
    startup: WmsProviderStartupConfiguration,
    session_factory: async_sessionmaker[AsyncSession],
) -> TransportRuntime:
    """构造一个进程/事件循环唯一的 Transport 运行时。"""

    # WMS Adapter 反向依赖 Transport 合同；实现依赖只在装配时加载，避免公共包初始化环。
    from src.app.wms_adapter.factory import build_wms_client
    from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
    from src.app.wms_adapter.transport_event_handler import TransportEventHandler

    validate_transport_runtime_profile(startup)
    client = build_wms_client(
        base_url=startup.compiled_profile.profile.server_url,
        timeout_seconds=10.0,
    )
    try:
        repository = TransportRepository()
        adapter = WmsTransportAdapter(
            client,
            submit_path=startup.compiled_profile.transport_submit_path,
        )
        service = TransportService(
            session_factory,
            repository,
            adapter,
            event_publisher=event_stream_service,
        )
        handler = TransportEventHandler(service)
        return TransportRuntime(
            client=client,
            repository=repository,
            adapter=adapter,
            service=service,
            handler=handler,
        )
    except BaseException:
        try:
            await client.aclose()
        except BaseException as cleanup_exc:
            logger.warning(
                "Transport runtime 构造失败后的 client 清理未完成: "
                f"type={type(cleanup_exc).__name__}, error={cleanup_exc!r}"
            )
        raise


__all__ = ["TransportRuntime", "build_transport_runtime", "validate_transport_runtime_profile"]
