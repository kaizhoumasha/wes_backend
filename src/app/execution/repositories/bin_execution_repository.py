"""BinExecution 持久化与固定 advisory lock owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.locks import bin_execution_lock_identity, epoch_lifecycle_lock_identity
from src.app.execution.models.bin_execution import BinExecution, BinExecutionStatus
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
from src.database.base_repository import BaseRepository


class BinExecutionRepository(BaseRepository[BinExecution]):
    def __init__(self) -> None:
        super().__init__(BinExecution)

    async def lock_epoch_lifecycle(self, db: AsyncSession, line_run_epoch_id: int) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": epoch_lifecycle_lock_identity(line_run_epoch_id)},
        )

    async def get_active_epoch_for_update(self, db: AsyncSession, line_run_epoch_id: int) -> LineRunEpoch | None:
        columns = cast("Any", LineRunEpoch).__table__.c
        return await db.scalar(
            select(LineRunEpoch)
            .where(columns.id == line_run_epoch_id, columns.status == LineRunEpochStatus.ACTIVE)
            .with_for_update()
        )

    async def lock_bin_execution(self, db: AsyncSession, bin_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": bin_execution_lock_identity(bin_id)},
        )

    async def get_active_by_bin_for_update(self, db: AsyncSession, bin_id: str) -> BinExecution | None:
        columns = cast("Any", BinExecution).__table__.c
        return await db.scalar(
            select(BinExecution)
            .where(columns.bin_id == bin_id, columns.status == BinExecutionStatus.ACTIVE)
            .with_for_update()
        )

    async def get_by_id_for_update(self, db: AsyncSession, execution_id: int) -> BinExecution | None:
        columns = cast("Any", BinExecution).__table__.c
        return await db.scalar(select(BinExecution).where(columns.id == execution_id).with_for_update())

    async def add(self, db: AsyncSession, execution: BinExecution) -> BinExecution:
        db.add(execution)
        await db.flush()
        return execution

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()


bin_execution_repository = BinExecutionRepository()

__all__ = ["BinExecutionRepository", "bin_execution_repository"]
