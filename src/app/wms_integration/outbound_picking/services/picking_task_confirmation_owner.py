"""PickingTask confirmation 的业务 owner 校验。"""

from __future__ import annotations

from src.app.wms_adapter.outbound_picking.arrival_report_wire import RETURN_RACK_ARRIVAL_REPORT_OPERATION
from src.app.wms_adapter.outbound_picking.completion_confirm_wire import COMPLETION_CONFIRM_OPERATION
from src.app.wms_adapter.outbound_picking.departure_wire import RACK_DEPARTURE_OPERATION
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BIN_INBOUND_BATCH_OPERATION
from src.app.wms_adapter.outbound_picking.material_decide_wire import MATERIAL_DECIDE_OPERATION
from src.app.wms_adapter.outbound_picking.movement_report_wire import MATERIAL_MOVEMENT_REPORT_OPERATION
from src.app.wms_adapter.outbound_picking.source_empty_wire import SOURCE_EMPTY_OPERATION
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_adapter.outbound_picking.work_plan_wire import BIN_WORK_PLAN_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories import PickingTaskRepository, picking_task_repository
from src.app.workline.repositories import WorkLineRepository


class PickingTaskConfirmationOwnerService:
    """供中立 WmsConfirmation dispatcher 校验 PickingTask owner，不处理业务状态推进。"""

    def __init__(
        self, repository: PickingTaskRepository | None = None, *, worklines: WorkLineRepository | None = None
    ) -> None:
        self._tasks = repository or picking_task_repository
        self._worklines = worklines or WorkLineRepository()

    async def lock_authority_root(self, db: object, *, picking_task_id: int) -> bool:
        workline_id = await self._tasks.get_workline_id(db, picking_task_id)  # type: ignore[arg-type]
        if workline_id is None:
            task = await self._tasks.get_by_id_for_update(db, picking_task_id)  # type: ignore[arg-type]
            return task is None or task.workline_id is None
        if not isinstance(workline_id, int) or isinstance(workline_id, bool) or workline_id <= 0:
            return False
        workline = await self._worklines.get_for_authority_update(db, workline_id)  # type: ignore[arg-type]
        if workline is None:
            return False
        task = await self._tasks.get_by_id_for_update(db, picking_task_id)  # type: ignore[arg-type]
        return task is None or task.workline_id == workline_id

    async def validate_dispatch_owner(
        self,
        db: object,
        *,
        picking_task_id: int,
        operation: str,
    ) -> bool:
        if operation in {RETURN_RACK_ARRIVAL_REPORT_OPERATION, MATERIAL_MOVEMENT_REPORT_OPERATION}:
            # 仅续送已冻结的事实义务；响应后的业务应用仍独立校验原 owner。
            return True
        return await self.validate_response_owner(db, picking_task_id=picking_task_id, operation=operation)

    async def validate_response_owner(
        self,
        db: object,
        *,
        picking_task_id: int,
        operation: str,
    ) -> bool:
        if operation not in {
            PICKING_TASK_PREPARE_OPERATION,
            COMPLETION_CONFIRM_OPERATION,
            MATERIAL_MOVEMENT_REPORT_OPERATION,
            RETURN_RACK_ARRIVAL_REPORT_OPERATION,
            BIN_INBOUND_BATCH_OPERATION,
            BIN_WORK_PLAN_OPERATION,
            RACK_DEPARTURE_OPERATION,
            MATERIAL_DECIDE_OPERATION,
            SOURCE_EMPTY_OPERATION,
        }:
            return False
        allowed_states = {PickingTaskStatus.PREPARING}
        if operation == PICKING_TASK_PREPARE_OPERATION:
            allowed_states.add(PickingTaskStatus.CANCELLED)
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
        if operation in {RACK_DEPARTURE_OPERATION, MATERIAL_MOVEMENT_REPORT_OPERATION}:
            allowed_states = {PickingTaskStatus.EXECUTING, PickingTaskStatus.EXECUTION_COMPLETED}
        if operation == COMPLETION_CONFIRM_OPERATION:
            allowed_states = {PickingTaskStatus.PREPARING, PickingTaskStatus.EXECUTING}
        # 归档只结束新业务准入；归档前冻结的 WMS 可靠义务仍须闭合原 identity。
        allowed_states.add(PickingTaskStatus.ARCHIVED)
        task = await self._tasks.get_by_id_for_update(db, picking_task_id)  # type: ignore[arg-type]
        return bool(
            task is not None
            and PickingTaskStatus(task.status) in allowed_states
            and isinstance(task.workline_id, int)
            and task.workline_id > 0
        )


__all__ = ["PickingTaskConfirmationOwnerService"]
