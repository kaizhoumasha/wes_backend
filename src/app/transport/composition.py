"""Transport 暗装配入口，不注册任何生产消费者。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.transport.repository import TransportRepository
from src.app.transport.service import TransportService
from src.app.wms_adapter.transport_adapter import WmsTransportAdapter

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.contracts import TransportOutcomePublisher
    from src.app.wms_adapter.client import WmsClient


def build_transport_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    wms_client: WmsClient,
    outcome_publisher: TransportOutcomePublisher,
) -> TransportService:
    """显式构造暗运行 Transport；生产接线由后续阶段负责。"""

    return TransportService(
        session_factory,
        TransportRepository(),
        WmsTransportAdapter(wms_client),
        outcome_publisher,
    )


__all__ = ["build_transport_service"]
