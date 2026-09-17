"""PickingTask 的单 WorkLine 原子 prepare Coordinator。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from wes_plugin_sdk import wms_operations
from wes_plugin_sdk.prepare_policy import PickingTaskPreparePolicy, PrepareContext, PrepareTaskType

from src.app.execution.config import WMS_CONFIRMATION_DISPATCH_WINDOW
from src.app.execution.services import (
    WmsConfirmationAcceptance,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.typed import encode_request
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.wms_integration.outbound_picking.repositories import (
    PickingTaskRepository,
    picking_task_repository,
)
from src.app.workline.repositories import (
    WorkLineRepository,
)
from src.app.workline.repositories import (
    workline_repository as default_workline_repository,
)
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.execution.models import WmsConfirmation
    from src.core.task_queue_gateway import TaskQueueGateway

logger = logging.getLogger(__name__)


class PickingTaskPrepareNoopReason(StrEnum):
    WORKLINE_NOT_READY = "WORKLINE_NOT_READY"
    NO_ELIGIBLE_TASK = "NO_ELIGIBLE_TASK"
    SELECTED_TASK_NOT_NEXT = "SELECTED_TASK_NOT_NEXT"


@dataclass(frozen=True, slots=True)
class PickingTaskPrepareResult:
    prepared: bool
    reason: PickingTaskPrepareNoopReason | None = None
    task: PickingTask | None = None
    confirmation: WmsConfirmation | None = None


class ConfirmationLifecyclePort(Protocol):
    async def create_or_get(self, db: object, **kwargs: object) -> object: ...


class PickingTaskPrepareCoordinator:
    """在一个事务内冻结 WorkLine/WorkLine/任务并创建可靠 prepare 义务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        policy: PickingTaskPreparePolicy,
        workline_repository: WorkLineRepository | None = None,
        task_repository: PickingTaskRepository | None = None,
        confirmation_service: ConfirmationLifecyclePort | None = None,
        task_queue_gateway: TaskQueueGateway,
        workline_reserved: Callable[[AsyncSession, int], Awaitable[bool]] | None = None,
    ) -> None:
        self._policy = policy
        self._sessions = session_factory
        self._worklines = workline_repository or default_workline_repository
        self._tasks = task_repository or picking_task_repository
        self._confirmations = confirmation_service or WmsConfirmationLifecycleService()
        self._task_queue = task_queue_gateway
        self._workline_reserved = workline_reserved

    async def prepare_next_for_workline(
        self,
        workline_id: int,
        *,
        expected_task_id: str | None = None,
        wms_workline_code: str | None = None,
        now: datetime | None = None,
    ) -> PickingTaskPrepareResult:
        if not isinstance(workline_id, int) or isinstance(workline_id, bool) or workline_id <= 0:
            raise ValueError("workline_id 必须是正整数")
        current = timezone.to_db_datetime(now) if now is not None else timezone.now_for_db()
        if current is None:
            raise ValueError("now 必须是有效时间")
        prepared: PickingTaskPrepareResult
        async with self._sessions.begin() as db:
            workline = await self._worklines.get_for_update(db, workline_id)
            if workline is None:
                return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY)
            prepared = await self.prepare_next_in_session(
                db, workline, expected_task_id=expected_task_id, wms_workline_code=wms_workline_code, now=current
            )
        if not prepared.prepared:
            return prepared
        try:
            self._task_queue.enqueue_wms_confirmations()
        except Exception:
            logger.exception("outbound_picking.prepare_enqueue_failed")
        return prepared

    async def prepare_next_in_session(
        self,
        db: AsyncSession,
        workline: object,
        *,
        expected_task_id: str | None = None,
        wms_workline_code: str | None = None,
        now: datetime | None = None,
    ) -> PickingTaskPrepareResult:
        """调用方已持有 WorkLine 行锁；claim、绑定与 Confirmation 共用当前事务。"""
        workline_id = getattr(workline, "id", None)
        if type(workline_id) is not int or workline_id <= 0:
            raise ValueError("workline_id 必须是正整数")
        current = timezone.to_db_datetime(now) if now is not None else timezone.now_for_db()
        if current is None:
            raise ValueError("now 必须是有效时间")
        if self._workline_reserved is not None and await self._workline_reserved(db, workline_id):
            return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY)
        task_type = self._policy.select_task_type(
            PrepareContext(
                getattr(workline, "is_active", False) is True,
                getattr(workline, "line_type", None),
                getattr(workline, "run_mode", None),
                getattr(workline, "plugin_key", None),
                getattr(workline, "flow_mode", None),
            )
        )
        if task_type is None:
            return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY)
        if not isinstance(task_type, PrepareTaskType):
            raise TypeError("prepare Policy 必须返回 PrepareTaskType")
        if await self._tasks.has_active_for_workline(db, workline_id):
            return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY)
        task = await self._tasks.claim_next_queued(
            db,
            workline_id=workline_id,
            task_type=PickingTaskType(task_type.value),
            now_ms=_timestamp_ms(current),
        )
        if task is None:
            return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.NO_ELIGIBLE_TASK)
        if expected_task_id is not None and task.task_id != expected_task_id:
            return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.SELECTED_TASK_NOT_NEXT)
        task_id = getattr(task, "id", None)
        line_code = wms_workline_code or getattr(workline, "line_code", None)
        if not isinstance(task_id, int) or task_id <= 0 or not isinstance(line_code, str):
            raise RuntimeError("prepare 冻结对象缺少持久身份")
        operation_id = new_uuid7()
        intent = wms_operations.outbound_picking_task_prepare(
            operation_id=operation_id,
            task_id=task.task_id,
            work_line_code=line_code,
        )
        request = encode_request(intent, timestamp=_timestamp_ms(current))
        task.status = PickingTaskStatus.PREPARING
        await self._tasks.flush(db)
        acceptance = await self._confirmations.create_or_get(
            db,
            operation=PICKING_TASK_PREPARE_OPERATION,
            operation_id=operation_id,
            picking_task_id=task_id,
            request_payload=request,
            deadline_at=current + WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=current,
        )
        if not isinstance(acceptance, WmsConfirmationAcceptance) or acceptance.duplicate:
            raise RuntimeError("新 prepare identity 未创建唯一 WmsConfirmation")
        return PickingTaskPrepareResult(True, task=task, confirmation=acceptance.confirmation)


def _timestamp_ms(value: datetime) -> int:
    return int(timezone.to_utc(value).timestamp() * 1000)


__all__ = [
    "PickingTaskPrepareCoordinator",
    "PickingTaskPrepareNoopReason",
    "PickingTaskPrepareResult",
]
