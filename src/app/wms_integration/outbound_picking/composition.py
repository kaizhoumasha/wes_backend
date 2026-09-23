"""WMS PickingTask 入站组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.wms_adapter.outbound_picking.cancel_event_handler import PickingTaskCancelHandler
from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedHandler
from src.app.wms_adapter.outbound_picking.manual_bin_completed_event_handler import ManualBinCompletedHandler
from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_event_handler import ManualRackDirectPickHandler
from src.app.wms_adapter.outbound_picking.plan_delta_event_handler import PickingTaskPlanDeltaHandler
from src.app.wms_adapter.outbound_picking.queue_changed_event_handler import PickingTaskQueueChangedHandler
from src.app.wms_integration.outbound_picking.services import PickingTaskCancelService, PickingTaskIssuedService
from src.app.wms_integration.outbound_picking.services.manual_bin_completed import ManualBinCompletedService
from src.app.wms_integration.outbound_picking.services.manual_rack_direct_pick_completed import (
    ManualRackDirectPickCompletedService,
)
from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PickingTaskPlanDeltaService
from src.app.wms_integration.outbound_picking.services.picking_task_queue_changed import PickingTaskQueueChangedService

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from wes_plugin_sdk import PickingTaskPlanAdmissionPolicy

    from src.core.task_queue_gateway import TaskQueueGateway


@dataclass(frozen=True, slots=True)
class OutboundPickingRuntime:
    picking_task_issued_handler: PickingTaskIssuedHandler
    picking_task_cancel_handler: PickingTaskCancelHandler
    picking_task_plan_delta_handler: PickingTaskPlanDeltaHandler
    picking_task_queue_changed_handler: PickingTaskQueueChangedHandler
    manual_bin_completed_handler: ManualBinCompletedHandler
    manual_rack_direct_pick_handler: ManualRackDirectPickHandler


def build_outbound_picking_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    prepare_plugin_identities: tuple[tuple[str, str], ...] = (),
    plan_activation_plugin_identities: tuple[tuple[str, str], ...] = (),
    plan_admission_policies: Mapping[tuple[str, str], PickingTaskPlanAdmissionPolicy] | None = None,
    task_queue_gateway: TaskQueueGateway | None = None,
    transport_service: object | None = None,
) -> OutboundPickingRuntime:
    service = PickingTaskIssuedService(
        session_factory,
        prepare_plugin_identities=prepare_plugin_identities,
        task_queue_gateway=task_queue_gateway,
    )
    cancel = PickingTaskCancelService(session_factory, transport_service=transport_service)
    plan_delta = PickingTaskPlanDeltaService(
        session_factory,
        plan_activation_plugin_identities=plan_activation_plugin_identities,
        plan_admission_policies=plan_admission_policies,
        task_queue_gateway=task_queue_gateway,
    )
    queue_changed = PickingTaskQueueChangedService(session_factory)
    manual_bin_completed = ManualBinCompletedService(
        session_factory,
        task_queue_gateway=task_queue_gateway,
    )
    manual_rack_direct_pick = ManualRackDirectPickCompletedService(session_factory)
    return OutboundPickingRuntime(
        picking_task_issued_handler=PickingTaskIssuedHandler(service),
        picking_task_cancel_handler=PickingTaskCancelHandler(cancel),
        picking_task_plan_delta_handler=PickingTaskPlanDeltaHandler(plan_delta),
        picking_task_queue_changed_handler=PickingTaskQueueChangedHandler(queue_changed),
        manual_bin_completed_handler=ManualBinCompletedHandler(manual_bin_completed),
        manual_rack_direct_pick_handler=ManualRackDirectPickHandler(manual_rack_direct_pick),
    )


__all__ = ["OutboundPickingRuntime", "build_outbound_picking_runtime"]
