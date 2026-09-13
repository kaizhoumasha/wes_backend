"""PickingTask prepare 的静态、插件条件化后台入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async

if TYPE_CHECKING:
    from src.app.wms_integration.outbound_picking.services import PickingTaskPrepareBatchService

_PREPARE_BATCH_LIMIT = 100


def _current_service() -> PickingTaskPrepareBatchService:
    runtime = celery_async_runtime.execution_runtime
    if runtime is None:
        raise RuntimeError("Deployment runtime is unavailable in the current Celery child")
    return runtime.picking_task_prepare_service


@celery_app.task(name="src.celery_app.tasks.picking_task_prepare.prepare_picking_tasks_batch")
def prepare_picking_tasks_batch(limit: int = _PREPARE_BATCH_LIMIT) -> int:
    if type(limit) is not int or limit != _PREPARE_BATCH_LIMIT:
        raise ValueError(f"prepare batch limit must be {_PREPARE_BATCH_LIMIT}")

    async def _prepare() -> int:
        return await _current_service().prepare_batch(limit=limit)

    return run_async(_prepare)


__all__ = ["prepare_picking_tasks_batch"]
