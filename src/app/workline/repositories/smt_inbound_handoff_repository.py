"""SMT 入库 handoff Repository。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.models.workline import WorkLine
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

    async def get_demand_by_handling_operation_key(
        self,
        db: AsyncSession,
        handling_operation_key: str,
    ) -> SmtInboundHandoffDemand | None:
        """按 full-box exchange handling operation key 查询 handoff demand。"""

        columns = cast("Any", SmtInboundHandoffDemand).__table__.c
        result = await db.execute(
            select(SmtInboundHandoffDemand).where(columns.handling_operation_key == handling_operation_key)
        )
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

    async def list_source_items(
        self,
        db: AsyncSession,
        handoff_demand_id: int,
    ) -> list[SmtInboundHandoffSourceItem]:
        """读取 demand 下的 source item，按 item_key 稳定排序。"""

        columns = cast("Any", SmtInboundHandoffSourceItem).__table__.c
        result = await db.execute(
            select(SmtInboundHandoffSourceItem)
            .where(columns.handoff_demand_id == handoff_demand_id)
            .order_by(columns.item_key)
        )
        return list(result.scalars().all())

    async def list_sorting_candidate_worklines(self, db: AsyncSession) -> list[WorkLine]:
        """读取 SMT 入库分拣 WorkLine 配置候选。"""

        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(
            select(WorkLine)
            .where(
                columns.plugin_key == "SMT_SORTING_INBOUND",
                columns.is_active.is_(True),
            )
            .order_by(columns.line_code.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    def build_ready_source_item_claim_statement(self, *, now: Any, limit: int = 1) -> Any:
        """构建 READY source item claim SQL，供 PostgreSQL SKIP LOCKED 使用。"""

        columns = cast("Any", SmtInboundHandoffSourceItem).__table__.c
        return (
            select(SmtInboundHandoffSourceItem)
            .where(
                columns.status == SmtInboundHandoffSourceItemStatus.READY.value,
                or_(columns.next_attempt_at.is_(None), columns.next_attempt_at <= now),
            )
            .order_by(columns.next_attempt_at.asc().nullsfirst(), columns.handoff_demand_id.asc(), columns.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def claim_next_ready_source_item(
        self,
        db: AsyncSession,
        *,
        now: Any,
    ) -> SmtInboundHandoffSourceItem | None:
        """使用 READY claim statement 认领下一条 source item。"""

        result = await db.execute(self.build_ready_source_item_claim_statement(now=now, limit=1))
        return result.scalars().first()

    async def get_source_item_by_id(
        self,
        db: AsyncSession,
        source_item_id: int,
    ) -> SmtInboundHandoffSourceItem | None:
        """按 ID 读取 source item。"""

        return await db.get(SmtInboundHandoffSourceItem, source_item_id)

    async def get_source_item_for_update(
        self,
        db: AsyncSession,
        source_item_id: int,
    ) -> SmtInboundHandoffSourceItem | None:
        """按 ID 加锁读取 source item，用于 claim 后 correlation 回写。"""

        columns = cast("Any", SmtInboundHandoffSourceItem).__table__.c
        result = await db.execute(
            select(SmtInboundHandoffSourceItem).where(columns.id == source_item_id).with_for_update()
        )
        return result.scalar_one_or_none()

    def build_due_recovery_demand_statement(self, *, now: Any, limit: int = 100) -> Any:
        """构建 handoff demand 兜底扫描 SQL。"""

        columns = cast("Any", SmtInboundHandoffDemand).__table__.c
        return (
            select(SmtInboundHandoffDemand)
            .where(
                columns.status.in_(
                    [
                        SmtInboundHandoffDemandStatus.CREATED.value,
                        SmtInboundHandoffDemandStatus.EVALUATING.value,
                        SmtInboundHandoffDemandStatus.FULL_BOX_EXCHANGED.value,
                        SmtInboundHandoffDemandStatus.READY_FOR_SORTING.value,
                    ]
                ),
                or_(columns.next_attempt_at.is_(None), columns.next_attempt_at <= now),
            )
            .order_by(columns.next_attempt_at.asc().nullsfirst(), columns.updated_at.asc(), columns.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def list_due_recovery_demands(
        self,
        db: AsyncSession,
        *,
        now: Any,
        limit: int = 100,
    ) -> list[SmtInboundHandoffDemand]:
        """读取到期 demand 兜底扫描候选。"""

        result = await db.execute(self.build_due_recovery_demand_statement(now=now, limit=limit))
        return list(result.scalars().all())

    def build_stuck_source_item_recovery_statement(
        self,
        *,
        now: Any,
        stale_after_seconds: int = 300,
        limit: int = 100,
    ) -> Any:
        """构建 claim 后卡住 source item 的恢复扫描 SQL。"""

        columns = cast("Any", SmtInboundHandoffSourceItem).__table__.c
        stale_cutoff = now - timedelta(seconds=max(stale_after_seconds, 1))
        return (
            select(SmtInboundHandoffSourceItem)
            .where(
                columns.status.in_(
                    [
                        SmtInboundHandoffSourceItemStatus.PICK_REQUESTED.value,
                        SmtInboundHandoffSourceItemStatus.CLAIMED_BY_SORTING.value,
                    ]
                ),
                columns.source_pick_inbox_id.isnot(None),
                columns.updated_at <= stale_cutoff,
            )
            .order_by(columns.updated_at.asc(), columns.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def list_stuck_source_items_for_recovery(
        self,
        db: AsyncSession,
        *,
        now: Any,
        limit: int = 100,
        stale_after_seconds: int = 300,
    ) -> list[SmtInboundHandoffSourceItem]:
        """读取 claim 后卡住的 source item 恢复候选。"""

        result = await db.execute(
            self.build_stuck_source_item_recovery_statement(
                now=now,
                stale_after_seconds=stale_after_seconds,
                limit=limit,
            )
        )
        return list(result.scalars().all())

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
