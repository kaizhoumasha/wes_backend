"""WorklineDispatchAttempt Repository 层。"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.runtime.orchestration.models.dispatch_attempt import DispatchAttemptStatus, WorklineDispatchAttempt
from src.database.base_repository import BaseRepository


class WorklineDispatchAttemptRepository(BaseRepository[WorklineDispatchAttempt]):
    """工作线派发尝试数据访问层。"""

    def __init__(self) -> None:
        super().__init__(WorklineDispatchAttempt)

    async def get_by_lease_token(self, db: AsyncSession, lease_token: str) -> WorklineDispatchAttempt | None:
        """按 lease token 查询派发尝试。"""

        columns = cast("Any", WorklineDispatchAttempt).__table__.c
        result = await db.execute(select(WorklineDispatchAttempt).where(columns.lease_token == lease_token))
        return result.scalar_one_or_none()

    async def get_by_outbox_id(self, db: AsyncSession, outbox_id: int) -> list[WorklineDispatchAttempt]:
        """按 outbox 查询派发尝试历史。"""

        columns = cast("Any", WorklineDispatchAttempt).__table__.c
        result = await db.execute(
            select(WorklineDispatchAttempt)
            .where(columns.outbox_id == outbox_id)
            .order_by(columns.attempt_no.asc(), columns.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_expired_dispatching_for_update(
        self,
        db: AsyncSession,
        *,
        outbox_id: int,
        lease_token: str,
        now: Any,
    ) -> WorklineDispatchAttempt | None:
        """锁定与过期 outbox fence 一致的活动 attempt。"""

        columns = cast("Any", WorklineDispatchAttempt).__table__.c
        result = await db.execute(
            select(WorklineDispatchAttempt)
            .where(
                columns.outbox_id == outbox_id,
                columns.lease_token == lease_token,
                columns.status == DispatchAttemptStatus.DISPATCHING,
                columns.lease_expires_at <= now,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()


workline_dispatch_attempt_repository = WorklineDispatchAttemptRepository()


__all__ = ["WorklineDispatchAttemptRepository", "workline_dispatch_attempt_repository"]
