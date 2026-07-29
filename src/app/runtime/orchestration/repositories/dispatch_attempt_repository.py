"""WorklineDispatchAttempt Repository 层。"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.sys.models.outbox import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus
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

    async def list_expired_dispatching_for_finished_outboxes_for_update(
        self,
        db: AsyncSession,
        *,
        now: Any,
        operation_domains: tuple[str, ...] | None = None,
        exclude_operation_domains: tuple[str, ...] | None = None,
        operation_identities: tuple[str, ...] | None = None,
        exclude_operation_identities: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> tuple[WorklineDispatchAttempt, ...]:
        """锁定终态 outbox 遗留的过期活动 attempt。"""

        attempt_columns = cast("Any", WorklineDispatchAttempt).__table__.c
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        predicates: list[Any] = [
            attempt_columns.status == DispatchAttemptStatus.DISPATCHING,
            attempt_columns.lease_expires_at <= now,
            outbox_columns.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP,
            outbox_columns.status.in_(
                [
                    SystemOutboxStatus.SENT,
                    SystemOutboxStatus.FAILED,
                    SystemOutboxStatus.UNKNOWN,
                    SystemOutboxStatus.CANCELLED,
                ]
            ),
            outbox_columns.finished_at.is_not(None),
        ]
        if operation_domains:
            predicates.append(outbox_columns.operation_domain.in_(operation_domains))
        if exclude_operation_domains:
            predicates.append(outbox_columns.operation_domain.not_in(exclude_operation_domains))
        if operation_identities:
            predicates.append(outbox_columns.operation_identity.in_(operation_identities))
        if exclude_operation_identities:
            predicates.append(outbox_columns.operation_identity.not_in(exclude_operation_identities))
        result = await db.execute(
            select(WorklineDispatchAttempt)
            .join(SystemOutbox, outbox_columns.id == attempt_columns.outbox_id)
            .where(*predicates)
            .order_by(attempt_columns.lease_expires_at, attempt_columns.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(result.scalars().all())


workline_dispatch_attempt_repository = WorklineDispatchAttemptRepository()


__all__ = ["WorklineDispatchAttemptRepository", "workline_dispatch_attempt_repository"]
