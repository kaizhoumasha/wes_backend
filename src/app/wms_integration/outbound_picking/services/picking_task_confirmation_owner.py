"""PickingTask prepare confirmation 的业务 owner 校验。"""

from __future__ import annotations

from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories import PickingTaskRepository, picking_task_repository


class PickingTaskConfirmationOwnerService:
    """供中立 WmsConfirmation dispatcher 校验 PickingTask owner，不处理业务状态推进。"""

    def __init__(self, repository: PickingTaskRepository | None = None) -> None:
        self._tasks = repository or picking_task_repository

    async def validate_prepare_response_owner(
        self,
        db: object,
        *,
        picking_task_id: int,
        operation: str,
    ) -> bool:
        if operation != PICKING_TASK_PREPARE_OPERATION:
            return False
        task = await self._tasks.get_by_id_for_update(db, picking_task_id)  # type: ignore[arg-type]
        return bool(
            task is not None
            and PickingTaskStatus(task.status) is PickingTaskStatus.PREPARING
            and isinstance(task.workline_id, int)
            and task.workline_id > 0
            and isinstance(task.line_run_epoch_id, int)
            and task.line_run_epoch_id > 0
        )


__all__ = ["PickingTaskConfirmationOwnerService"]
