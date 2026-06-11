"""SMT 入库 handoff Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffSourceItem,
)
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SmtInboundHandoffRepository(BaseRepository[SmtInboundHandoffDemand]):
    """SMT 入库 handoff demand/source item 数据访问层。"""

    def __init__(self) -> None:
        super().__init__(SmtInboundHandoffDemand)

    async def get_demand_by_release_id(
        self,
        db: AsyncSession,
        rack_release_id: str,
    ) -> SmtInboundHandoffDemand | None:
        """按 release fact 稳定 ID 查询 handoff demand。"""

        columns = cast("Any", SmtInboundHandoffDemand).__table__.c
        result = await db.execute(select(SmtInboundHandoffDemand).where(columns.rack_release_id == rack_release_id))
        return result.scalar_one_or_none()

    async def create_or_get_demand_by_release(
        self,
        db: AsyncSession,
        data: dict[str, Any],
    ) -> SmtInboundHandoffDemand:
        """按 rack_release_id 原子创建 demand；冲突时返回已有 demand。"""

        rack_release_id = str(data["rack_release_id"])
        table = cast("Any", SmtInboundHandoffDemand).__table__
        insert_fn = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgresql_insert
        statement = (
            insert_fn(table)
            .values(**self._model_insert_values(SmtInboundHandoffDemand, data))
            .on_conflict_do_nothing(index_elements=[table.c.rack_release_id])
            .returning(table.c.id)
        )
        result = await db.execute(statement)
        created_id = result.scalar_one_or_none()
        if isinstance(created_id, int):
            created = await self.get_by_id(db, created_id)
            if created is not None:
                return created

        existing = await self.get_demand_by_release_id(db, rack_release_id)
        if existing is not None:
            return existing
        raise RuntimeError(f"创建 SMT 入库 handoff demand 后无法读取: {rack_release_id}")

    async def create_source_items_idempotent(
        self,
        db: AsyncSession,
        items: list[dict[str, Any]],
    ) -> None:
        """幂等创建 source items；重复 item_key 不回滚当前事务。"""

        if not items:
            return
        table = cast("Any", SmtInboundHandoffSourceItem).__table__
        insert_fn = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgresql_insert
        values = [self._model_insert_values(SmtInboundHandoffSourceItem, item) for item in items]
        statement = (
            insert_fn(table)
            .values(values)
            .on_conflict_do_nothing(index_elements=[table.c.handoff_demand_id, table.c.item_key])
        )
        await db.execute(statement)
        await db.flush()

    @staticmethod
    def _model_insert_values(model: type[Any], data: dict[str, Any]) -> dict[str, Any]:
        instance = model(**data)
        table = cast("Any", model).__table__
        values: dict[str, Any] = {}
        for column in table.columns:
            value = getattr(instance, column.name)
            if column.primary_key and value is None:
                continue
            values[column.name] = value
        return values


smt_inbound_handoff_repository = SmtInboundHandoffRepository()


__all__ = [
    "SmtInboundHandoffRepository",
    "smt_inbound_handoff_repository",
]
