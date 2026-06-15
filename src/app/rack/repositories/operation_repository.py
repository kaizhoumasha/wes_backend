"""货架级任务 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.rack.models.operation import RackOperation, RackOperationStatus, RackTask, RackTaskStatus, RackTaskType
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RackOperationRepository(BaseRepository[RackOperation]):
    """货架业务操作 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackOperation)

    async def get_by_operation_key(self, db: AsyncSession, operation_key: str) -> RackOperation | None:
        """按操作幂等键查询。"""

        columns = cast("Any", RackOperation).__table__.c
        result = await db.execute(select(RackOperation).where(columns.operation_key == operation_key))
        return result.scalar_one_or_none()

    async def mark_status(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        operation_status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        result_json_patch: dict[str, Any] | None = None,
    ) -> RackOperation | None:
        """更新 operation 派生状态。"""

        operation = await self.get_by_operation_key(db, operation_key)
        if operation is None:
            return None
        now = timezone.now_for_db()
        operation.operation_status = RackOperationStatus(operation_status)
        operation.result_json = {
            **(operation.result_json or {}),
            "operation_status": operation_status,
            **(result_json_patch or {}),
        }
        operation.error_code = error_code
        operation.error_message = error_message
        if operation_status in {
            RackOperationStatus.SUCCEEDED.value,
            RackOperationStatus.FAILED.value,
            RackOperationStatus.RECONCILING.value,
            RackOperationStatus.CANCELLED.value,
        }:
            operation.completed_at = now
        db.add(operation)
        return operation


class RackTaskRepository(BaseRepository[RackTask]):
    """货架级任务 Repository。"""

    def __init__(self) -> None:
        super().__init__(RackTask)

    async def get_by_task_key(self, db: AsyncSession, task_key: str) -> RackTask | None:
        """按任务幂等键查询。"""

        columns = cast("Any", RackTask).__table__.c
        result = await db.execute(select(RackTask).where(columns.task_key == task_key))
        return result.scalar_one_or_none()

    async def get_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> RackTask | None:
        """按外部派发键查询。"""

        columns = cast("Any", RackTask).__table__.c
        result = await db.execute(select(RackTask).where(columns.dispatch_key == dispatch_key))
        return result.scalar_one_or_none()

    async def get_by_operation_sequence(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        sequence_no: int,
    ) -> RackTask | None:
        """按 operation_key + sequence_no 查询单个任务。"""

        columns = cast("Any", RackTask).__table__.c
        result = await db.execute(
            select(RackTask).where(
                columns.operation_key == operation_key,
                columns.sequence_no == sequence_no,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_operation_key(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
    ) -> list[RackTask]:
        """查询同一货架 operation 下的全部任务，按序号升序。"""

        columns = cast("Any", RackTask).__table__.c
        result = await db.execute(
            select(RackTask).where(columns.operation_key == operation_key).order_by(columns.sequence_no.asc())
        )
        return list(result.scalars().all())

    async def list_active_by_target_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        target_position_code: str,
    ) -> list[RackTask]:
        """查询目标位置当前未闭环的货架任务。"""

        columns = cast("Any", RackTask).__table__.c
        result = await db.execute(
            select(RackTask)
            .where(
                columns.workline_code == workline_code,
                columns.target_position_code == target_position_code,
                columns.task_status.in_(_ACTIVE_STATUSES),
            )
            .order_by(columns.operation_key.asc(), columns.sequence_no.asc())
        )
        return list(result.scalars().all())

    async def list_move_rack_source_claims(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        source_position_code: str,
        rack_code: str,
    ) -> list[RackTask]:
        """查询源位置 + 货架当前仍占用 claim 的 MOVE_RACK 任务。"""

        columns = cast("Any", RackTask).__table__.c
        result = await db.execute(
            select(RackTask)
            .where(
                columns.workline_code == workline_code,
                columns.source_position_code == source_position_code,
                columns.rack_code == rack_code,
                columns.task_type == RackTaskType.MOVE_RACK,
                columns.task_status.in_(_SOURCE_RACK_CLAIM_STATUSES),
            )
            .order_by(columns.operation_key.asc(), columns.sequence_no.asc())
        )
        return list(result.scalars().all())

    async def cancel_active_by_material_session(
        self,
        db: AsyncSession,
        *,
        material_session_id: int,
        reason: str,
    ) -> int:
        """取消指定物料 session 当前未闭环的货架任务，释放位置 claim。"""

        columns = cast("Any", RackTask).__table__.c
        result = await db.execute(
            select(RackTask)
            .where(
                columns.material_session_id == material_session_id,
                columns.task_status.in_(_ACTIVE_STATUSES),
            )
            .with_for_update()
        )
        tasks = list(result.scalars().all())
        now = timezone.now_for_db()
        for task in tasks:
            task.task_status = RackTaskStatus.CANCELLED.value
            task.error_code = reason
            task.error_message = reason
            task.completed_at = now
            task.result_json = {
                **(task.result_json or {}),
                "status": RackTaskStatus.CANCELLED.value,
                "reason": reason,
            }
        return len(tasks)


_ACTIVE_STATUSES = (
    RackTaskStatus.PLANNED,
    RackTaskStatus.REQUESTED,
    RackTaskStatus.IN_PROGRESS,
    RackTaskStatus.RECONCILING,
)
_SOURCE_RACK_CLAIM_STATUSES = (
    RackTaskStatus.PLANNED,
    RackTaskStatus.REQUESTED,
    RackTaskStatus.IN_PROGRESS,
    RackTaskStatus.RECONCILING,
)


rack_task_repository = RackTaskRepository()
rack_operation_repository = RackOperationRepository()


__all__ = ["RackOperationRepository", "RackTaskRepository", "rack_operation_repository", "rack_task_repository"]
