"""自动联调应用基于已构造的 Transport 基础运行时装配。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.transport_debug.debug_run_service import TransportDebugRunService
from src.app.transport_debug.repository import TransportDebugRunRepository
from src.core.task_queue_gateway import task_queue_gateway

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.composition import TransportRuntime
    from src.app.transport_debug.reset_service import TransportDebugResetService


def build_transport_debug_run_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transport_runtime: TransportRuntime,
) -> TransportDebugRunService:
    return TransportDebugRunService(
        session_factory,
        TransportDebugRunRepository(),
        transport_runtime.service,
        task_queue_gateway=task_queue_gateway,
    )


def build_transport_debug_reset_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transport_runtime: TransportRuntime,
) -> TransportDebugResetService:
    from src.app.transport_debug.reset_service import TransportDebugResetService

    return TransportDebugResetService(
        session_factory,
        transport_runtime.repository,
        TransportDebugRunRepository(),
    )
