"""Runtime Hold repository."""

from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.runtime_hold import NgReturnItem, NgReturnItemStatus, RuntimeHold, RuntimeHoldStatus
from src.database.base_repository import BaseRepository

_ACTIVE_BLOCKING_STATUSES = (
    RuntimeHoldStatus.OPEN,
    RuntimeHoldStatus.IN_PROGRESS,
    RuntimeHoldStatus.REOPENED,
)

_ACTIVE_NG_RETURN_STATUSES = (
    NgReturnItemStatus.WAITING_REWORK,
    NgReturnItemStatus.REWORKING,
)


class RuntimeHoldRepository(BaseRepository[RuntimeHold]):
    """Runtime Hold 数据访问层。"""

    def __init__(self) -> None:
        super().__init__(RuntimeHold)

    async def get_for_update(self, db: AsyncSession, hold_id: int) -> RuntimeHold | None:
        """根据 ID 查询并锁定 RuntimeHold。"""

        columns = cast("Any", RuntimeHold).__table__.c
        result = await db.execute(select(RuntimeHold).where(columns.id == hold_id).with_for_update())
        return result.scalar_one_or_none()

    async def get_by_source_idempotency_key(
        self,
        db: AsyncSession,
        source_idempotency_key: str,
    ) -> RuntimeHold | None:
        """按 source idempotency key 查询 RuntimeHold。"""

        columns = cast("Any", RuntimeHold).__table__.c
        result = await db.execute(
            select(RuntimeHold).where(columns.source_idempotency_key == source_idempotency_key).limit(1)
        )
        return result.scalar_one_or_none()

    async def create_open_hold(self, db: AsyncSession, **data: Any) -> RuntimeHold:
        """幂等创建 open RuntimeHold。"""

        source_idempotency_key = data.get("source_idempotency_key")
        if not isinstance(source_idempotency_key, str) or not source_idempotency_key:
            raise ValueError("source_idempotency_key is required")

        existing = await self.get_by_source_idempotency_key(db, source_idempotency_key)
        if existing is not None:
            return existing

        try:
            async with db.begin_nested():
                hold = RuntimeHold(**data)
                db.add(hold)
                await db.flush()
            return hold
        except IntegrityError:
            existing = await self.get_by_source_idempotency_key(db, source_idempotency_key)
            if existing is not None:
                return existing
            raise

    async def get_active_blocking_by_workline(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> list[RuntimeHold]:
        """查询 WorkLine 当前 active blocking RuntimeHold。"""

        columns = cast("Any", RuntimeHold).__table__.c
        result = await db.execute(
            select(RuntimeHold)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(_ACTIVE_BLOCKING_STATUSES),
                columns.blocking.is_(True),
            )
            .order_by(columns.created_at.asc(), columns.id.asc())
        )
        return list(result.scalars().all())

    async def count_active_by_workline(self, db: AsyncSession, workline_id: int) -> int:
        """统计 WorkLine 当前 active blocking RuntimeHold 数量。"""

        columns = cast("Any", RuntimeHold).__table__.c
        result = await db.execute(
            select(func.count(columns.id)).where(
                columns.workline_id == workline_id,
                columns.status.in_(_ACTIVE_BLOCKING_STATUSES),
                columns.blocking.is_(True),
            )
        )
        return int(result.scalar_one() or 0)

    async def count_open_issues_by_device(
        self,
        db: AsyncSession,
        *,
        device_ids: list[int] | tuple[int, ...] | set[int] | None = None,
    ) -> dict[int, int]:
        """按 source device 聚合 active RuntimeHold 数量。"""

        columns = cast("Any", RuntimeHold).__table__.c
        query = (
            select(columns.source_device_id, func.count(columns.id))
            .where(
                columns.source_device_id.isnot(None),
                columns.status.in_(_ACTIVE_BLOCKING_STATUSES),
                columns.blocking.is_(True),
            )
            .group_by(columns.source_device_id)
        )
        if device_ids:
            query = query.where(columns.source_device_id.in_(list(device_ids)))

        result = await db.execute(query)
        return {int(device_id): int(count) for device_id, count in result.all() if device_id is not None}

    async def list_holds(
        self,
        db: AsyncSession,
        *,
        workline_id: int | None = None,
        session_id: int | None = None,
        status: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[RuntimeHold]:
        """查询 RuntimeHold 列表，包含没有 source_device_id 的 session/workline 级 Hold。"""

        columns = cast("Any", RuntimeHold).__table__.c
        query = select(RuntimeHold)
        if workline_id is not None:
            query = query.where(columns.workline_id == workline_id)
        if session_id is not None:
            query = query.where(columns.session_id == session_id)
        if status is not None:
            query = query.where(columns.status == status)
        elif active_only:
            query = query.where(columns.status.in_(_ACTIVE_BLOCKING_STATUSES), columns.blocking.is_(True))

        result = await db.execute(query.order_by(columns.created_at.desc(), columns.id.desc()).limit(limit))
        return list(result.scalars().all())

    async def list_active_by_device_ids(
        self,
        db: AsyncSession,
        *,
        device_ids: list[int] | tuple[int, ...] | set[int],
    ) -> dict[int, list[RuntimeHold]]:
        """按 source device 聚合 active RuntimeHold。"""

        if not device_ids:
            return {}
        columns = cast("Any", RuntimeHold).__table__.c
        result = await db.execute(
            select(RuntimeHold)
            .where(
                columns.source_device_id.in_(list(device_ids)),
                columns.status.in_(_ACTIVE_BLOCKING_STATUSES),
                columns.blocking.is_(True),
            )
            .order_by(columns.source_device_id.asc(), columns.created_at.asc(), columns.id.asc())
        )
        mapping: dict[int, list[RuntimeHold]] = {}
        for hold in result.scalars().all():
            if hold.source_device_id is None:
                continue
            mapping.setdefault(hold.source_device_id, []).append(hold)
        return mapping

    async def find_latest_for_projection(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        session_id: int | None = None,
        source_outbox_id: int | None = None,
        source_command_id: int | None = None,
        source_device_id: int | None = None,
    ) -> RuntimeHold | None:
        """查找 Sandbox/历史投影可链接的 RuntimeHold。"""

        predicates: list[Any] = []
        columns = cast("Any", RuntimeHold).__table__.c
        if session_id is not None:
            predicates.append(columns.session_id == session_id)
        if source_outbox_id is not None:
            predicates.append(columns.source_outbox_id == source_outbox_id)
        if source_command_id is not None:
            predicates.append(columns.source_command_id == source_command_id)
        if source_device_id is not None:
            predicates.append(columns.source_device_id == source_device_id)
        if not predicates:
            return None

        result = await db.execute(
            select(RuntimeHold)
            .where(columns.workline_id == workline_id, or_(*predicates))
            .order_by(columns.created_at.desc(), columns.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_ng_return_items(
        self,
        db: AsyncSession,
        *,
        runtime_hold_id: int | None = None,
        status: str | None = None,
        material_identity_key: str | None = None,
        limit: int = 100,
    ) -> list[NgReturnItem]:
        """查询 NG return item。"""

        columns = cast("Any", NgReturnItem).__table__.c
        query = select(NgReturnItem)
        if runtime_hold_id is not None:
            query = query.where(columns.created_from_runtime_hold_id == runtime_hold_id)
        if status is not None:
            query = query.where(columns.status == status)
        if material_identity_key is not None:
            query = query.where(columns.material_identity_key == material_identity_key)

        result = await db.execute(query.order_by(columns.created_at.desc(), columns.id.desc()).limit(limit))
        return list(result.scalars().all())

    async def get_active_ng_return_item_by_material_identity(
        self,
        db: AsyncSession,
        material_identity_key: str,
        *,
        exclude_runtime_hold_id: int | None = None,
    ) -> NgReturnItem | None:
        """查找同一物料当前是否已有 active NG return item。"""

        columns = cast("Any", NgReturnItem).__table__.c
        query = select(NgReturnItem).where(
            columns.material_identity_key == material_identity_key,
            columns.status.in_(_ACTIVE_NG_RETURN_STATUSES),
        )
        if exclude_runtime_hold_id is not None:
            query = query.where(columns.created_from_runtime_hold_id != exclude_runtime_hold_id)

        result = await db.execute(query.order_by(columns.created_at.asc(), columns.id.asc()).limit(1))
        return result.scalar_one_or_none()


runtime_hold_repository = RuntimeHoldRepository()


__all__ = [
    "RuntimeHoldRepository",
    "runtime_hold_repository",
]
