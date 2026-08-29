"""BinExecution 生命周期服务。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Protocol

from src.app.execution.models.bin_execution import BinExecution, BinExecutionStatus
from src.app.execution.repositories.bin_execution_repository import bin_execution_repository
from src.app.execution.repositories.position_projection_repository import position_projection_repository


class ActiveBinExecutionExistsError(ValueError):
    """同一 bin 已存在活动执行。"""


class BinExecutionNotActiveError(ValueError):
    """BinExecution 不存在或已关闭。"""


class BinExecutionRepositoryPort(Protocol):
    async def lock_epoch_lifecycle(self, db: object, line_run_epoch_id: int) -> None: ...

    async def get_active_epoch_for_update(self, db: object, line_run_epoch_id: int) -> object | None: ...

    async def lock_bin_execution(self, db: object, bin_id: str) -> None: ...

    async def get_active_by_bin_for_update(self, db: object, bin_id: str) -> BinExecution | None: ...

    async def get_by_id_for_update(self, db: object, execution_id: int) -> BinExecution | None: ...

    async def add(self, db: object, execution: BinExecution) -> BinExecution: ...

    async def flush(self, db: object) -> None: ...


class PositionProjectionCleanupPort(Protocol):
    async def lock_projection(self, db: object, object_type: str, object_id: str) -> None: ...

    async def delete_for_bin_execution(self, db: object, bin_execution_id: int) -> None: ...


class BinExecutionService:
    def __init__(
        self,
        *,
        repository: BinExecutionRepositoryPort = bin_execution_repository,
        projection_repository: PositionProjectionCleanupPort = position_projection_repository,
    ) -> None:
        self._repository = repository
        self._projections = projection_repository

    async def create(
        self,
        db: object,
        *,
        execution_code: str,
        bin_id: str,
        workline_id: int,
        line_run_epoch_id: int,
        started_at: datetime,
    ) -> BinExecution:
        await self._repository.lock_epoch_lifecycle(db, line_run_epoch_id)
        epoch = await self._repository.get_active_epoch_for_update(db, line_run_epoch_id)
        if epoch is None or getattr(epoch, "workline_id", None) != workline_id:
            raise ValueError("BinExecution requires the matching active LineRunEpoch")
        await self._repository.lock_bin_execution(db, bin_id)
        if await self._repository.get_active_by_bin_for_update(db, bin_id) is not None:
            raise ActiveBinExecutionExistsError(f"bin {bin_id} already has an active execution")
        return await self._repository.add(
            db,
            BinExecution(
                execution_code=execution_code,
                bin_id=bin_id,
                workline_id=workline_id,
                line_run_epoch_id=line_run_epoch_id,
                started_at=started_at,
            ),
        )

    async def close(self, db: object, execution: BinExecution, *, closed_at: datetime) -> BinExecution:
        if execution.id is None:
            raise ValueError("BinExecution must be persisted before close")
        await self._repository.lock_epoch_lifecycle(db, execution.line_run_epoch_id)
        current = await self._repository.get_by_id_for_update(db, execution.id)
        if current is None or current.status != BinExecutionStatus.ACTIVE:
            raise BinExecutionNotActiveError(f"BinExecution {execution.id} is not active")
        await self._projections.lock_projection(db, "BIN", current.bin_id)
        await self._projections.delete_for_bin_execution(db, execution.id)
        current.status = BinExecutionStatus.CLOSED
        current.closed_at = closed_at
        await self._repository.flush(db)
        return current


bin_execution_service = BinExecutionService()

__all__ = [
    "ActiveBinExecutionExistsError",
    "BinExecutionNotActiveError",
    "BinExecutionService",
    "bin_execution_service",
]
