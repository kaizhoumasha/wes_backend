"""WMS PickingTask 入站组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedHandler
from src.app.wms_integration.outbound_picking.services import PickingTaskIssuedService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class OutboundPickingRuntime:
    picking_task_issued_handler: PickingTaskIssuedHandler


def build_outbound_picking_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> OutboundPickingRuntime:
    service = PickingTaskIssuedService(session_factory)
    return OutboundPickingRuntime(
        picking_task_issued_handler=PickingTaskIssuedHandler(service),
    )


__all__ = ["OutboundPickingRuntime", "build_outbound_picking_runtime"]
