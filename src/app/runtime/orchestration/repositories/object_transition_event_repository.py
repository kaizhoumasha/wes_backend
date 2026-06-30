"""ObjectTransitionEvent Repository 层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.runtime.orchestration.models.object_transition_event import ObjectTransitionDomain, ObjectTransitionEvent
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ObjectTransitionEventRepository(BaseRepository[ObjectTransitionEvent]):
    """统一对象状态迁移事件数据访问层。"""

    def __init__(self) -> None:
        super().__init__(ObjectTransitionEvent)

    async def get_by_idempotency_key(
        self,
        db: AsyncSession,
        idempotency_key: str,
    ) -> ObjectTransitionEvent | None:
        """按派生幂等键查询迁移事件。"""

        columns = cast("Any", ObjectTransitionEvent).__table__.c
        result = await db.execute(select(ObjectTransitionEvent).where(columns.idempotency_key == idempotency_key))
        return result.scalar_one_or_none()

    @staticmethod
    def _insert_values(data: dict[str, Any]) -> dict[str, Any]:
        event = ObjectTransitionEvent(**data)
        table = cast("Any", ObjectTransitionEvent).__table__
        values: dict[str, Any] = {}
        for column in table.columns:
            value = getattr(event, column.name)
            if column.primary_key and value is None:
                continue
            values[column.name] = value
        return values

    async def create_idempotent_by_key(
        self,
        db: AsyncSession,
        data: dict[str, Any],
    ) -> ObjectTransitionEvent:
        """按 idempotency_key 原子创建；冲突时返回已有记录。"""

        idempotency_key = str(data["idempotency_key"])
        table = cast("Any", ObjectTransitionEvent).__table__
        dialect_name = db.get_bind().dialect.name
        insert_fn = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        statement = (
            insert_fn(table)
            .values(**self._insert_values(data))
            .on_conflict_do_nothing(
                index_elements=[table.c.idempotency_key],
                index_where=text("idempotency_key IS NOT NULL"),
            )
            .returning(table.c.id)
        )
        result = await db.execute(statement)
        created_id = result.scalar_one_or_none()
        if isinstance(created_id, int):
            created = await self.get_by_id(db, created_id)
            if created is not None:
                return created

        existing = await self.get_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            return existing
        raise RuntimeError(f"创建对象迁移事件失败: {idempotency_key}")

    async def list_by_trace_id(self, db: AsyncSession, trace_id: str) -> list[ObjectTransitionEvent]:
        """按 trace_id 查询迁移事件。"""

        columns = cast("Any", ObjectTransitionEvent).__table__.c
        result = await db.execute(
            select(ObjectTransitionEvent)
            .where(columns.trace_id == trace_id)
            .order_by(columns.occurred_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_by_workline_session_id(
        self,
        db: AsyncSession,
        workline_session_id: int,
    ) -> list[ObjectTransitionEvent]:
        """按 workline_session_id 查询迁移事件。"""

        columns = cast("Any", ObjectTransitionEvent).__table__.c
        result = await db.execute(
            select(ObjectTransitionEvent)
            .where(columns.workline_session_id == workline_session_id)
            .order_by(columns.occurred_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_by_object(
        self,
        db: AsyncSession,
        *,
        domain: ObjectTransitionDomain | str,
        object_type: str,
        object_key: str,
    ) -> list[ObjectTransitionEvent]:
        """按对象业务键查询迁移事件。"""

        columns = cast("Any", ObjectTransitionEvent).__table__.c
        result = await db.execute(
            select(ObjectTransitionEvent)
            .where(
                columns.domain == _domain_value(domain),
                columns.object_type == object_type,
                columns.object_key == object_key,
            )
            .order_by(columns.occurred_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_by_source_event(
        self,
        db: AsyncSession,
        *,
        domain: ObjectTransitionDomain | str,
        source_event_id: str,
    ) -> list[ObjectTransitionEvent]:
        """按业务域和来源事实事件查询迁移事件。"""

        columns = cast("Any", ObjectTransitionEvent).__table__.c
        result = await db.execute(
            select(ObjectTransitionEvent)
            .where(
                columns.domain == _domain_value(domain),
                columns.source_event_id == source_event_id,
            )
            .order_by(columns.occurred_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())


def _domain_value(domain: ObjectTransitionDomain | str) -> str:
    return domain.value if isinstance(domain, ObjectTransitionDomain) else str(domain)


object_transition_event_repository = ObjectTransitionEventRepository()


__all__ = ["ObjectTransitionEventRepository", "object_transition_event_repository"]
