"""RuntimeLocationEvent Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.runtime.orchestration.models.runtime_location_event import RuntimeLocationEvent
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RuntimeLocationEventRepository(BaseRepository[RuntimeLocationEvent]):
    """作业期位置事实数据访问层。"""

    def __init__(self) -> None:
        super().__init__(RuntimeLocationEvent)

    async def get_by_idempotency_key(
        self,
        db: AsyncSession,
        idempotency_key: str,
    ) -> RuntimeLocationEvent | None:
        """按幂等键查询位置事实。"""

        columns = cast("Any", RuntimeLocationEvent).__table__.c
        result = await db.execute(select(RuntimeLocationEvent).where(columns.idempotency_key == idempotency_key))
        return result.scalar_one_or_none()

    @staticmethod
    def _insert_values(data: dict[str, Any]) -> dict[str, Any]:
        event = RuntimeLocationEvent(**data)
        table = cast("Any", RuntimeLocationEvent).__table__
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
    ) -> RuntimeLocationEvent:
        """按 idempotency_key 原子创建；冲突时返回已有记录。"""

        idempotency_key = str(data["idempotency_key"])
        table = cast("Any", RuntimeLocationEvent).__table__
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
        raise RuntimeError(f"创建位置事实失败: {idempotency_key}")

    async def list_by_object(
        self,
        db: AsyncSession,
        *,
        object_type: str,
        object_key: str,
    ) -> list[RuntimeLocationEvent]:
        """按对象业务键查询位置事实历史。"""

        columns = cast("Any", RuntimeLocationEvent).__table__.c
        result = await db.execute(
            select(RuntimeLocationEvent)
            .where(columns.object_type == object_type, columns.object_key == object_key)
            .order_by(columns.occurred_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_by_correlation_id(
        self,
        db: AsyncSession,
        *,
        correlation_id: str,
    ) -> list[RuntimeLocationEvent]:
        """按 correlation_id 查询位置事实历史。"""

        columns = cast("Any", RuntimeLocationEvent).__table__.c
        result = await db.execute(
            select(RuntimeLocationEvent)
            .where(columns.correlation_id == correlation_id)
            .order_by(columns.occurred_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def list_by_external_reference(
        self,
        db: AsyncSession,
        *,
        external_reference_type: str,
        external_reference_value: str,
        provider_code: str | None = None,
    ) -> list[RuntimeLocationEvent]:
        """按外部引用查询位置事实历史。"""

        columns = cast("Any", RuntimeLocationEvent).__table__.c
        predicates = [
            columns.external_reference_type == external_reference_type,
            columns.external_reference_value == external_reference_value,
        ]
        if provider_code is not None:
            predicates.append(columns.provider_code == provider_code)
        result = await db.execute(
            select(RuntimeLocationEvent).where(*predicates).order_by(columns.occurred_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())


runtime_location_event_repository = RuntimeLocationEventRepository()


__all__ = ["RuntimeLocationEventRepository", "runtime_location_event_repository"]
