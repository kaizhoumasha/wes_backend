"""WMS PickingTask 入站组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedHandler
from src.app.wms_adapter.outbound_picking.plan_delta_event_handler import PickingTaskPlanDeltaHandler
from src.app.wms_adapter.outbound_picking.queue_changed_event_handler import PickingTaskQueueChangedHandler
from src.app.wms_integration.outbound_picking.services import PickingTaskIssuedService
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PickingTaskPlanDeltaService
from src.app.wms_integration.outbound_picking.services.picking_task_queue_changed import PickingTaskQueueChangedService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class OutboundPickingRuntime:
    picking_task_issued_handler: PickingTaskIssuedHandler
    picking_task_plan_delta_handler: PickingTaskPlanDeltaHandler
    picking_task_queue_changed_handler: PickingTaskQueueChangedHandler
    plan_delta_service: PickingTaskPlanDeltaService


def build_outbound_picking_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> OutboundPickingRuntime:
    service = PickingTaskIssuedService(session_factory)
    plan_delta = PickingTaskPlanDeltaService(session_factory)
    queue_changed = PickingTaskQueueChangedService(session_factory)
    return OutboundPickingRuntime(
        picking_task_issued_handler=PickingTaskIssuedHandler(service),
        picking_task_plan_delta_handler=PickingTaskPlanDeltaHandler(plan_delta),
        picking_task_queue_changed_handler=PickingTaskQueueChangedHandler(queue_changed),
        plan_delta_service=plan_delta,
    )


__all__ = ["OutboundPickingRuntime", "build_outbound_picking_runtime"]
