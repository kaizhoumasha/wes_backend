"""WorkLineRuntimeStatusProjection Repository 层。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.runtime.orchestration.workline_runtime_status_projection import (
    WorkLineRuntimeStatus,
    WorklineRuntimeStatusProjection,
)
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class EnsureDefaultProjectionResult:
    """确保默认投影的结果，区分本事务是否实际插入。"""

    projection: WorklineRuntimeStatusProjection
    created: bool


class WorklineRuntimeStatusProjectionRepository(BaseRepository[WorklineRuntimeStatusProjection]):
    """WorkLine runtime 状态投影数据访问层。"""

    def __init__(self) -> None:
        super().__init__(WorklineRuntimeStatusProjection)

    async def get_by_workline_id(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        for_update: bool = False,
        populate_existing: bool = False,
    ) -> WorklineRuntimeStatusProjection | None:
        """按 workline_id 读取投影；可选行级锁或强制刷新 identity map。"""

        columns = cast("Any", WorklineRuntimeStatusProjection).__table__.c
        statement = select(WorklineRuntimeStatusProjection).where(columns.workline_id == workline_id)
        if for_update:
            statement = statement.with_for_update()
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def list_by_workline_ids(
        self,
        db: AsyncSession,
        workline_ids: Sequence[int],
    ) -> dict[int, WorklineRuntimeStatusProjection]:
        """批量读取 WorkLine runtime 状态投影。"""

        ids = [int(workline_id) for workline_id in dict.fromkeys(workline_ids)]
        if not ids:
            return {}
        columns = cast("Any", WorklineRuntimeStatusProjection).__table__.c
        result = await db.execute(select(WorklineRuntimeStatusProjection).where(columns.workline_id.in_(ids)))
        return {projection.workline_id: projection for projection in result.scalars().all()}

    async def ensure_default(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> WorklineRuntimeStatusProjection:
        """显式确保 STOPPED 默认投影存在；普通读路径不能隐式调用。"""

        return (await self.ensure_default_result(db, workline_id)).projection

    async def ensure_default_result(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> EnsureDefaultProjectionResult:
        """显式确保默认投影存在，并返回本事务是否实际创建。"""

        existing = await self.get_by_workline_id(db, workline_id)
        if existing is not None:
            return EnsureDefaultProjectionResult(projection=existing, created=False)
        table = cast("Any", WorklineRuntimeStatusProjection).__table__
        values = {
            "workline_id": workline_id,
            "runtime_status": WorkLineRuntimeStatus.STOPPED.value,
            "source": "runtime/orchestration",
            "stopped_reason": "DEFAULT_PROJECTION",
            "evidence_json": {},
        }
        dialect_name = db.get_bind().dialect.name
        insert_fn = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        statement = (
            insert_fn(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[table.c.workline_id])
            .returning(table.c.id)
        )
        result = await db.execute(statement)
        projection_id = result.scalar_one_or_none()
        if projection_id is not None:
            projection = await self.get_by_id(db, projection_id)
            if projection is not None:
                return EnsureDefaultProjectionResult(projection=projection, created=True)

        projection = await self.get_by_workline_id(db, workline_id)
        if projection is None:
            raise RuntimeError(f"WorkLine runtime status projection default ensure failed: workline_id={workline_id}")
        return EnsureDefaultProjectionResult(projection=projection, created=False)

    async def upsert_status(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        runtime_status: str,
        source: str = "runtime/orchestration",
        stopped_at: Any | None = None,
        stopped_reason: str | None = None,
        resumed_at: Any | None = None,
        active_safety_incident_id: int | None = None,
        evidence_json: dict[str, Any] | None = None,
    ) -> WorklineRuntimeStatusProjection:
        """按 workline_id 原子 upsert runtime 状态投影。"""

        table = cast("Any", WorklineRuntimeStatusProjection).__table__
        values = {
            "workline_id": workline_id,
            "runtime_status": runtime_status,
            "source": source,
            "stopped_at": stopped_at,
            "stopped_reason": stopped_reason,
            "resumed_at": resumed_at,
            "active_safety_incident_id": active_safety_incident_id,
            "evidence_json": dict(evidence_json or {}),
        }
        dialect_name = db.get_bind().dialect.name
        insert_fn = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        statement = (
            insert_fn(table)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[table.c.workline_id],
                set_=values,
            )
            .returning(table.c.id)
        )
        result = await db.execute(statement)
        projection_id = result.scalar_one()
        projection = await self.get_by_id(db, projection_id)
        if projection is None:
            raise RuntimeError(f"WorkLine runtime status projection upsert failed: workline_id={workline_id}")
        return projection


workline_runtime_status_projection_repository = WorklineRuntimeStatusProjectionRepository()


__all__ = [
    "EnsureDefaultProjectionResult",
    "WorklineRuntimeStatusProjectionRepository",
    "workline_runtime_status_projection_repository",
]
