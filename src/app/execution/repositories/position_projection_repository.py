"""PositionProjection 的 current-only 持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.locks import epoch_lifecycle_lock_identity, position_projection_lock_identity
from src.app.execution.models.bin_execution import BinExecution
from src.app.execution.models.position_projection import PositionProjection
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochPositionBinding
from src.database.base_repository import BaseRepository


class PositionProjectionRepository(BaseRepository[PositionProjection]):
    def __init__(self) -> None:
        super().__init__(PositionProjection)

    async def lock_epoch_lifecycle(self, db: AsyncSession, line_run_epoch_id: int) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": epoch_lifecycle_lock_identity(line_run_epoch_id)},
        )

    async def get_epoch_for_update(self, db: AsyncSession, line_run_epoch_id: int) -> LineRunEpoch | None:
        columns = cast("Any", LineRunEpoch).__table__.c
        return await db.scalar(select(LineRunEpoch).where(columns.id == line_run_epoch_id).with_for_update())

    async def get_bin_execution_for_update(self, db: AsyncSession, bin_execution_id: int) -> BinExecution | None:
        columns = cast("Any", BinExecution).__table__.c
        return await db.scalar(select(BinExecution).where(columns.id == bin_execution_id).with_for_update())

    async def lock_projection(self, db: AsyncSession, object_type: str, object_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": position_projection_lock_identity(object_type, object_id)},
        )

    async def get(
        self,
        db: AsyncSession,
        object_type: str,
        object_id: str,
        *,
        for_update: bool = False,
    ) -> PositionProjection | None:
        columns = cast("Any", PositionProjection).__table__.c
        statement = select(PositionProjection).where(
            columns.object_type == object_type,
            columns.object_id == object_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    async def get_for_update(self, db: AsyncSession, object_type: str, object_id: str) -> PositionProjection | None:
        return await self.get(db, object_type, object_id, for_update=True)

    async def add(self, db: AsyncSession, projection: PositionProjection) -> PositionProjection:
        db.add(projection)
        return projection

    async def delete_for_bin_execution(self, db: AsyncSession, bin_execution_id: int) -> None:
        columns = cast("Any", PositionProjection).__table__.c
        await db.execute(delete(PositionProjection).where(columns.bin_execution_id == bin_execution_id))

    async def delete_for_epoch(self, db: AsyncSession, line_run_epoch_id: int) -> None:
        columns = cast("Any", PositionProjection).__table__.c
        await db.execute(delete(PositionProjection).where(columns.line_run_epoch_id == line_run_epoch_id))

    async def get_active_workline_summary(self, db: AsyncSession, workline_id: int) -> dict[str, Any]:
        """汇总仍在本线绑定位置或位置未知的 current projection。"""

        columns = cast("Any", PositionProjection).__table__.c
        bindings = cast("Any", LineRunEpochPositionBinding).__table__.c
        location_code = columns.position_json["location_code"].as_string()
        at_bound_position = exists(
            select(1).where(
                bindings.line_run_epoch_id == columns.line_run_epoch_id,
                bindings.location_id == location_code,
            )
        )
        result = await db.execute(
            select(PositionProjection, func.count().over().label("owner_count"))
            .where(
                columns.workline_id == workline_id,
                or_(columns.position_unknown.is_(True), at_bound_position),
            )
            .order_by(columns.object_type, columns.object_id, columns.id)
            .limit(1)
        )
        row = result.first()
        if row is None:
            return {"count": 0, "sample": None}
        projection = row[0]
        projection_location = None
        if isinstance(projection.position_json, dict):
            projection_location = projection.position_json.get("location_code")
        return {
            "count": int(row.owner_count),
            "sample": {
                "type": "position_projection",
                "id": str(projection.id),
                "status": "UNKNOWN" if projection.position_unknown else projection_location,
                "identity": f"{projection.object_type}:{projection.object_id}",
            },
        }

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()


position_projection_repository = PositionProjectionRepository()

__all__ = ["PositionProjectionRepository", "position_projection_repository"]
