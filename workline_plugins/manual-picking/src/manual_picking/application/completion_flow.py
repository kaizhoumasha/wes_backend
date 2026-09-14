"""人工拣料 PickingTask 完成确认；退箱和货架离场继续独立执行。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from wes_plugin_sdk import (
    PickingTaskBusinessInProgress,
    PickingTaskCompleted,
    PickingTaskPlanRevisionStale,
    wms_operations,
)

from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class ManualPickingCompletionFlow:
    def __init__(self, repository: Any, scheduler: Any, *, uuid_factory: Callable[[], str]) -> None:
        self._repository = repository
        self._scheduler = scheduler
        self._uuid_factory = uuid_factory

    async def advance_in_session(self, db: Any, line: Any, task: Any, *, now: datetime | None = None) -> int:  # noqa: PLR0911
        now = now or timezone.now_for_db()
        if task.status != PickingTaskStatus.EXECUTING or task.plan_blocked_evidence_id is not None:
            return 0
        latest = await self._repository.latest_confirmation(db, task.id)
        if latest is not None:
            if (
                latest.status != "COMPLETED"
                or latest.task_id != task.task_id
                or latest.outcome is None
                or latest.completed_at is None
            ):
                return 0
            result = latest.outcome.result
            if isinstance(result, PickingTaskCompleted):
                if latest.plan_revision != task.last_applied_plan_revision:
                    return 0
                task.status = PickingTaskStatus.EXECUTION_COMPLETED
                return 1
            if isinstance(result, PickingTaskPlanRevisionStale):
                if task.last_applied_plan_revision < result.current_plan_revision:
                    return 0
            elif isinstance(result, PickingTaskBusinessInProgress):
                if now < latest.completed_at + timedelta(milliseconds=result.retry_after_ms):
                    return 0
            else:
                return 0
        if not await self._repository.ready_to_confirm(db, line, task):
            return 0
        intent = wms_operations.outbound_picking_task_completion_confirm(
            operation_id=self._uuid_factory(),
            task_id=task.task_id,
            last_applied_plan_revision=task.last_applied_plan_revision,
        )
        await self._scheduler.create_in_session(db, intent, picking_task_id=task.id, created_at=now)
        return 1


__all__ = ["ManualPickingCompletionFlow"]
