"""WMS PickingTask 入站组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedHandler
from src.app.wms_adapter.outbound_picking.manual_bin_completed_event_handler import ManualBinCompletedHandler
from src.app.wms_adapter.outbound_picking.plan_delta_event_handler import PickingTaskPlanDeltaHandler
from src.app.wms_adapter.outbound_picking.queue_changed_event_handler import PickingTaskQueueChangedHandler
from src.app.wms_integration.outbound_picking.services import PickingTaskIssuedService
from src.app.wms_integration.outbound_picking.services.manual_bin_completed import ManualBinCompletedService
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PickingTaskPlanDeltaService
from src.app.wms_integration.outbound_picking.services.picking_task_queue_changed import PickingTaskQueueChangedService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class OutboundPickingRuntime:
    picking_task_issued_handler: PickingTaskIssuedHandler
    picking_task_plan_delta_handler: PickingTaskPlanDeltaHandler
    picking_task_queue_changed_handler: PickingTaskQueueChangedHandler
    manual_bin_completed_handler: ManualBinCompletedHandler
    plan_delta_service: PickingTaskPlanDeltaService


def build_outbound_picking_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> OutboundPickingRuntime:
    service = PickingTaskIssuedService(session_factory)
    plan_delta = PickingTaskPlanDeltaService(session_factory)
    queue_changed = PickingTaskQueueChangedService(session_factory)
    manual_bin_completed = ManualBinCompletedService(session_factory)
    return OutboundPickingRuntime(
        picking_task_issued_handler=PickingTaskIssuedHandler(service),
        picking_task_plan_delta_handler=PickingTaskPlanDeltaHandler(plan_delta),
        picking_task_queue_changed_handler=PickingTaskQueueChangedHandler(queue_changed),
        manual_bin_completed_handler=ManualBinCompletedHandler(manual_bin_completed),
        plan_delta_service=plan_delta,
    )


__all__ = ["OutboundPickingRuntime", "build_outbound_picking_runtime"]
