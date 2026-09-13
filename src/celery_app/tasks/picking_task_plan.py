"""已提交 PickingTask 计划的静态、插件条件化激活入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import celery_async_runtime, run_async

if TYPE_CHECKING:
    from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
        PickingTaskPlanActivationService,
    )

_PLAN_BATCH_LIMIT = 100


def _current_service() -> PickingTaskPlanActivationService:
    runtime = celery_async_runtime.execution_runtime
    if runtime is None:
        raise RuntimeError("Deployment runtime is unavailable in the current Celery child")
    return runtime.picking_task_plan_activation_service


@celery_app.task(name="src.celery_app.tasks.picking_task_plan.activate_picking_task_plans_batch")
def activate_picking_task_plans_batch(limit: int = _PLAN_BATCH_LIMIT) -> int:
    if type(limit) is not int or limit != _PLAN_BATCH_LIMIT:
        raise ValueError(f"plan activation batch limit must be {_PLAN_BATCH_LIMIT}")

    async def _activate() -> int:
        return await _current_service().activate_batch(limit=limit)

    return run_async(_activate)


__all__ = ["activate_picking_task_plans_batch"]
