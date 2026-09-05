"""人工 PickingTask 的单 WorkLine 原子 prepare 服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from src.app.execution.services import (
    WMS_CONFIRMATION_DISPATCH_WINDOW,
    WmsConfirmationAcceptance,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.outbound_picking.wire import (
    PICKING_TASK_PREPARE_OPERATION,
    parse_picking_task_prepare_request,
)
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories import (
    PickingTaskRepository,
    PickingWorklineEligibilityRepository,
    picking_task_repository,
    picking_workline_eligibility_repository,
)
from src.app.workline.models import LineType, WorkLineRunMode
from src.app.workline.repositories import (
    LineRunEpochRepository,
    WorkLineRepository,
    line_run_epoch_repository,
)
from src.app.workline.repositories import (
    workline_repository as default_workline_repository,
)
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.execution.models import WmsConfirmation
    from src.core.task_queue_gateway import TaskQueueGateway

logger = logging.getLogger(__name__)

MANUAL_PICKING_PLUGIN_KEY = "manual_bin_processing"
MANUAL_PICKING_FLOW_MODE = "MANUAL_BIN_PROCESSING"


class PickingTaskPrepareNoopReason(StrEnum):
    NO_ACTIVE_EPOCH = "NO_ACTIVE_EPOCH"
    WORKLINE_NOT_READY = "WORKLINE_NOT_READY"
    NO_ELIGIBLE_TASK = "NO_ELIGIBLE_TASK"


@dataclass(frozen=True, slots=True)
class PickingTaskPrepareResult:
    prepared: bool
    reason: PickingTaskPrepareNoopReason | None = None
    task: PickingTask | None = None
    confirmation: WmsConfirmation | None = None


class ConfirmationLifecyclePort(Protocol):
    async def create_or_get(self, db: object, **kwargs: object) -> object: ...


class PickingTaskPrepareService:
    """在一个事务内冻结 WorkLine/Epoch/任务并创建可靠 prepare 义务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        epoch_repository: LineRunEpochRepository | None = None,
        workline_repository: WorkLineRepository | None = None,
        eligibility_repository: PickingWorklineEligibilityRepository | None = None,
        task_repository: PickingTaskRepository | None = None,
        confirmation_service: ConfirmationLifecyclePort | None = None,
        task_queue_gateway: TaskQueueGateway,
    ) -> None:
        self._sessions = session_factory
        self._epochs = epoch_repository or line_run_epoch_repository
        self._worklines = workline_repository or default_workline_repository
        self._eligibility = eligibility_repository or picking_workline_eligibility_repository
        self._tasks = task_repository or picking_task_repository
        self._confirmations = confirmation_service or WmsConfirmationLifecycleService()
        self._task_queue = task_queue_gateway

    async def prepare_next_for_workline(
        self,
        workline_id: int,
        *,
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
            epoch = await self._epochs.get_active_for_workline(db, workline_id)
            epoch_id = getattr(epoch, "id", None)
            if not isinstance(epoch_id, int) or isinstance(epoch_id, bool) or epoch_id <= 0:
                return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.NO_ACTIVE_EPOCH)
            await self._epochs.lock_epoch_lifecycle(db, epoch_id)
            locked_epoch = await self._epochs.get_active_for_workline_for_update(db, workline_id)
            if not self._static_context_ready(workline, locked_epoch, epoch_id):
                return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY)
            if await self._tasks.has_active_for_workline(db, workline_id):
                return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY)
            task = await self._tasks.claim_next_manual(db, now_ms=_timestamp_ms(current))
            if task is None:
                return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.NO_ELIGIBLE_TASK)
            if not await self._runtime_context_ready(db, workline_id, epoch_id, current):
                return PickingTaskPrepareResult(False, PickingTaskPrepareNoopReason.WORKLINE_NOT_READY)
            task_id = getattr(task, "id", None)
            line_code = getattr(workline, "line_code", None)
            if not isinstance(task_id, int) or task_id <= 0 or not isinstance(line_code, str):
                raise RuntimeError("prepare 冻结对象缺少持久身份")
            operation_id = new_uuid7()
            request = parse_picking_task_prepare_request(
                {
                    "operation_id": operation_id,
                    "operation": PICKING_TASK_PREPARE_OPERATION,
                    "timestamp": _timestamp_ms(current),
                    "data": {"task_id": task.task_id, "workline_code": line_code},
                }
            )
            task.status = PickingTaskStatus.PREPARING
            task.workline_id = workline_id
            task.line_run_epoch_id = epoch_id
            await self._tasks.flush(db)
            acceptance = await self._confirmations.create_or_get(
                db,
                operation=PICKING_TASK_PREPARE_OPERATION,
                operation_id=operation_id,
                picking_task_id=task_id,
                request_payload=request.model_dump(mode="json"),
                deadline_at=current + WMS_CONFIRMATION_DISPATCH_WINDOW,
                created_at=current,
            )
            if not isinstance(acceptance, WmsConfirmationAcceptance) or acceptance.duplicate:
                raise RuntimeError("新 prepare identity 未创建唯一 WmsConfirmation")
            prepared = PickingTaskPrepareResult(True, task=task, confirmation=acceptance.confirmation)
        try:
            self._task_queue.enqueue_wms_confirmations()
        except Exception:
            logger.exception("outbound_picking.prepare_enqueue_failed")
        return prepared

    async def _runtime_context_ready(
        self,
        db: Any,
        workline_id: int,
        epoch_id: int,
        now: datetime,
    ) -> bool:
        summary = await self._worklines.get_unfinished_workload_summary(db, workline_id)
        by_type = summary.get("by_type") if isinstance(summary, dict) else None
        if not isinstance(by_type, dict) or by_type.get("line_run_epochs") != 1:
            return False
        if any(bool(blocked) for owner, blocked in by_type.items() if owner != "line_run_epochs"):
            return False
        return await self._eligibility.is_ready(
            db,
            workline_id=workline_id,
            line_run_epoch_id=epoch_id,
            now=now,
        )

    @staticmethod
    def _static_context_ready(workline: object, epoch: object, epoch_id: int) -> bool:
        return bool(
            workline is not None
            and getattr(workline, "is_active", False) is True
            and getattr(workline, "line_type", None) == LineType.MANUAL
            and getattr(workline, "run_mode", None) == WorkLineRunMode.AUTO
            and epoch is not None
            and getattr(epoch, "id", None) == epoch_id
            and getattr(epoch, "plugin_key", None) == MANUAL_PICKING_PLUGIN_KEY
            and getattr(epoch, "flow_mode", None) == MANUAL_PICKING_FLOW_MODE
        )


def _timestamp_ms(value: datetime) -> int:
    return int(timezone.to_utc(value).timestamp() * 1000)


__all__ = [
    "MANUAL_PICKING_FLOW_MODE",
    "MANUAL_PICKING_PLUGIN_KEY",
    "PickingTaskPrepareNoopReason",
    "PickingTaskPrepareResult",
    "PickingTaskPrepareService",
]
