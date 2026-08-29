"""MaterialExecution 持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.models.material_execution import MaterialExecution, MaterialExecutionStatus
from src.database.base_repository import BaseRepository


class MaterialExecutionRepository(BaseRepository[MaterialExecution]):
    def __init__(self) -> None:
        super().__init__(MaterialExecution)

    async def lock_material_trace(self, db: AsyncSession, material_trace_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:material_trace_id, 0))"),
            {"material_trace_id": material_trace_id},
        )

    async def get_active_by_trace_for_update(
        self,
        db: AsyncSession,
        material_trace_id: str,
    ) -> MaterialExecution | None:
        columns = cast("Any", MaterialExecution).__table__.c
        result = await db.execute(
            select(MaterialExecution)
            .where(
                columns.material_trace_id == material_trace_id,
                columns.status != MaterialExecutionStatus.CLOSED,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add(self, db: AsyncSession, execution: MaterialExecution) -> MaterialExecution:
        db.add(execution)
        await db.flush()
        return execution

    async def get_admission_head_for_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        line_run_epoch_id: int,
    ) -> MaterialExecution | None:
        """锁定同一 WorkLine/Epoch 中不可越过的最早活动物料。"""

        columns = cast("Any", MaterialExecution).__table__.c
        result = await db.execute(
            select(MaterialExecution)
            .where(
                columns.workline_id == workline_id,
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.status != MaterialExecutionStatus.CLOSED,
                columns.admission_received_at.is_not(None),
                columns.admission_evidence_id.is_not(None),
            )
            .order_by(
                columns.admission_received_at,
                columns.admission_evidence_id,
                columns.id,
            )
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, db: AsyncSession, execution_id: int) -> MaterialExecution | None:
        columns = cast("Any", MaterialExecution).__table__.c
        result = await db.execute(select(MaterialExecution).where(columns.id == execution_id).with_for_update())
        return result.scalar_one_or_none()

    async def get_by_execution_code_for_update(
        self,
        db: AsyncSession,
        execution_code: str,
    ) -> MaterialExecution | None:
        columns = cast("Any", MaterialExecution).__table__.c
        result = await db.execute(
            select(MaterialExecution).where(columns.execution_code == execution_code).with_for_update()
        )
        return result.scalar_one_or_none()

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()


material_execution_repository = MaterialExecutionRepository()

__all__ = ["MaterialExecutionRepository", "material_execution_repository"]
