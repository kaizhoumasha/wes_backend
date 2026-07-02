"""ConveyorQueueMembership Repository 层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ConveyorQueueMembershipRepository(BaseRepository[ConveyorQueueMembership]):
    """动态输送线队列 membership 数据访问层。"""

    def __init__(self) -> None:
        super().__init__(ConveyorQueueMembership)

    async def create_without_session_rollback(
        self,
        db: AsyncSession,
        data: dict[str, Any],
    ) -> ConveyorQueueMembership:
        """创建 membership；约束冲突交给调用方的 savepoint 隔离。"""

        membership = ConveyorQueueMembership(**data)
        db.add(membership)
        await db.flush()
        await db.refresh(membership)
        return membership

    async def get_active_by_bin_code(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        bin_code: str,
    ) -> ConveyorQueueMembership | None:
        """按 WorkLine + 真实料箱编码读取 ACTIVE membership。"""

        columns = cast("Any", ConveyorQueueMembership).__table__.c
        result = await db.execute(
            select(ConveyorQueueMembership).where(
                columns.workline_id == workline_id,
                columns.bin_code == bin_code,
                columns.membership_status == "ACTIVE",
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_placeholder_key(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        placeholder_key: str,
    ) -> ConveyorQueueMembership | None:
        """按 WorkLine + 未扫码占位键读取 ACTIVE membership。"""

        columns = cast("Any", ConveyorQueueMembership).__table__.c
        result = await db.execute(
            select(ConveyorQueueMembership).where(
                columns.workline_id == workline_id,
                columns.placeholder_key == placeholder_key,
                columns.membership_status == "ACTIVE",
            )
        )
        return result.scalar_one_or_none()

    async def list_active_by_identity(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
    ) -> list[ConveyorQueueMembership]:
        """按写入身份读取候选 ACTIVE memberships。"""

        identity_clauses = []
        columns = cast("Any", ConveyorQueueMembership).__table__.c
        if bin_code is not None:
            identity_clauses.append(columns.bin_code == bin_code)
        if placeholder_key is not None:
            identity_clauses.append(columns.placeholder_key == placeholder_key)
        if not identity_clauses:
            return []

        result = await db.execute(
            select(ConveyorQueueMembership)
            .where(
                columns.workline_id == workline_id,
                columns.membership_status == "ACTIVE",
                or_(*identity_clauses),
            )
            .order_by(columns.id.asc())
        )
        return list(result.scalars().all())


conveyor_queue_membership_repository = ConveyorQueueMembershipRepository()


__all__ = [
    "ConveyorQueueMembershipRepository",
    "conveyor_queue_membership_repository",
]
