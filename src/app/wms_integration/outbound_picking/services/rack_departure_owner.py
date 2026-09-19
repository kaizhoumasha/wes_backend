"""货架离场决定由活动 WorkLine 承担。"""

from src.app.wms_adapter.outbound_picking.departure_wire import parse_rack_departure_request
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.workline.repositories import WorkLineRepository


class RackDepartureOwnerService:
    def __init__(self, *, worklines=None, tasks=None):
        self._worklines = worklines or WorkLineRepository()
        self._tasks = tasks or PickingTaskRepository()

    async def validate_owner(self, db, *, workline_id, request_payload):
        try:
            request = parse_rack_departure_request(request_payload)
        except (ValueError, TypeError):
            return False
        workline = await self._worklines.get_for_update(db, workline_id, populate_existing=True)
        if workline is None or not workline.is_active:
            return False
        if request.data.task_id is None:
            return True
        task = await self._tasks.get_by_task_id_for_update(db, request.data.task_id)
        return bool(
            task is not None
            and task.workline_id == workline_id
            and task.status in {PickingTaskStatus.EXECUTING, PickingTaskStatus.EXECUTION_COMPLETED}
        )


__all__ = ["RackDepartureOwnerService"]
