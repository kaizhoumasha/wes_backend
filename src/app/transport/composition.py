"""Transport 唯一生产组合根。"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.core.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.execution.services.position_projection_service import PositionProjectionService
    from src.app.transport.contracts import TransportPort
    from src.app.wms_adapter.client import WmsClient
    from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
    from src.app.wms_adapter.transport_event_handler import TransportEventHandler


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
        position_projection_service: PositionProjectionService,
    ) -> None:
        self.client = client
        self.repository = repository
        self.adapter = adapter
        self.service = service
        self.port: TransportPort = service
        self.handler = handler
        self.position_projection_service = position_projection_service
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


async def build_transport_runtime(
    *,
    wms_base_url: str,
    transport_submit_path: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> TransportRuntime:
    """构造一个进程/事件循环唯一的 Transport 运行时。"""

    # WMS Adapter 反向依赖 Transport 合同；实现依赖只在装配时加载，避免公共包初始化环。
    from src.app.wms_adapter.factory import build_wms_client
    from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
    from src.app.wms_adapter.transport_event_handler import TransportEventHandler

    client = build_wms_client(
        base_url=wms_base_url,
        timeout_seconds=10.0,
    )
    try:
        from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository
        from src.app.execution.services.position_projection_service import PositionProjectionService

        repository = TransportRepository()
        position_projection_service = PositionProjectionService(repository=PositionProjectionRepository())
        adapter = WmsTransportAdapter(
            client,
            submit_path=transport_submit_path,
        )
        service = TransportService(
            session_factory,
            repository,
            adapter,
            position_projections=position_projection_service,
        )
        handler = TransportEventHandler(service)
        return TransportRuntime(
            client=client,
            repository=repository,
            adapter=adapter,
            service=service,
            handler=handler,
            position_projection_service=position_projection_service,
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


__all__ = ["TransportRuntime", "build_transport_runtime"]
