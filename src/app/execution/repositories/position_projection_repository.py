"""PositionProjection 的 current-only 持久化 owner。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.execution.locks import position_projection_lock_identity
from src.app.execution.models.position_projection import PositionProjection
from src.app.workline.models.workline import WorkLine
from src.database.base_repository import BaseRepository


class PositionProjectionRepository(BaseRepository[PositionProjection]):
    def __init__(self) -> None:
        super().__init__(PositionProjection)

    async def get_workline_for_update(self, db: AsyncSession, workline_id: int) -> WorkLine | None:
        from src.app.workline.repositories.workline_repository import workline_repository

        return await workline_repository.get_for_update(db, workline_id)

    async def is_workline_position(self, db: AsyncSession, workline_id: int, position: dict[str, Any] | None) -> bool:
        line = await db.get(WorkLine, workline_id)
        if line is None or position is None:
            return False
        return position.get("location_code") in {binding["location_id"] for binding in line.position_bindings.values()}

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

    async def get_active_workline_summary(self, db: AsyncSession, workline_id: int) -> dict[str, Any]:
        """汇总仍在本线绑定位置或位置未知的 current projection。"""

        columns = cast("Any", PositionProjection).__table__.c
        line = await db.get(WorkLine, workline_id)
        locations = (
            tuple(binding["location_id"] for binding in line.position_bindings.values()) if line is not None else ()
        )
        at_bound_position = columns.position_json["location_code"].as_string().in_(locations)
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
