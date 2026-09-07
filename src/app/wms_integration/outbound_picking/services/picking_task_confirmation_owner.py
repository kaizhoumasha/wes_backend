"""PickingTask confirmation 的业务 owner 校验。"""

from __future__ import annotations

from src.app.wms_adapter.outbound_picking.arrival_report_wire import RETURN_RACK_ARRIVAL_REPORT_OPERATION
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.material_decide_wire import MATERIAL_DECIDE_OPERATION
from src.app.wms_adapter.outbound_picking.source_empty_wire import SOURCE_EMPTY_OPERATION
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_adapter.outbound_picking.work_plan_wire import BIN_WORK_PLAN_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories import PickingTaskRepository, picking_task_repository


class PickingTaskConfirmationOwnerService:
    """供中立 WmsConfirmation dispatcher 校验 PickingTask owner，不处理业务状态推进。"""

    def __init__(self, repository: PickingTaskRepository | None = None) -> None:
        self._tasks = repository or picking_task_repository

    async def validate_response_owner(
        self,
        db: object,
        *,
        picking_task_id: int,
        operation: str,
    ) -> bool:
        if operation not in {
            PICKING_TASK_PREPARE_OPERATION,
            RETURN_RACK_ARRIVAL_REPORT_OPERATION,
            BIN_INBOUND_BATCH_OPERATION,
            BIN_WORK_PLAN_OPERATION,
            RACK_DEPARTURE_OPERATION,
            MATERIAL_DECIDE_OPERATION,
            SOURCE_EMPTY_OPERATION,
        }:
            return False
        allowed_states = {PickingTaskStatus.PREPARING}
        if operation == RETURN_RACK_ARRIVAL_REPORT_OPERATION:
            # 已冻结的到位事实义务跨任务推进继续派发，不以业务完成代替 WMS 确认。
            allowed_states |= {PickingTaskStatus.EXECUTING, PickingTaskStatus.EXECUTION_COMPLETED}
        if operation in {
            BIN_INBOUND_BATCH_OPERATION,
            BIN_WORK_PLAN_OPERATION,
            MATERIAL_DECIDE_OPERATION,
            SOURCE_EMPTY_OPERATION,
        }:
            allowed_states = {PickingTaskStatus.EXECUTING}
        if operation == RACK_DEPARTURE_OPERATION:
            allowed_states = {PickingTaskStatus.EXECUTING, PickingTaskStatus.EXECUTION_COMPLETED}
        task = await self._tasks.get_by_id_for_update(db, picking_task_id)  # type: ignore[arg-type]
        return bool(
            task is not None
            and PickingTaskStatus(task.status) in allowed_states
            and isinstance(task.workline_id, int)
            and task.workline_id > 0
            and isinstance(task.workline_id, int)
            and task.workline_id > 0
        )


__all__ = ["PickingTaskConfirmationOwnerService"]
