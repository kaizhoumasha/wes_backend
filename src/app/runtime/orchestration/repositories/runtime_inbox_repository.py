"""RuntimeInbox 跨域只读 Repository 实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select

from src.app.contracts.runtime_inbox_query import (
    RUNTIME_INBOX_UNFINISHED_STATUSES,
    RuntimeInboxEvidence,
    RuntimeInboxWorkloadSample,
)
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RuntimeInboxRepository(BaseRepository[RuntimeInbox]):
    """集中持有 RuntimeInbox ORM，并向业务 repository 暴露 typed query DTO。"""

    def __init__(self) -> None:
        super().__init__(RuntimeInbox)

    async def get_evidence_by_id(self, db: AsyncSession, inbox_id: int) -> RuntimeInboxEvidence | None:
        """按显式主键返回跨域只读 evidence DTO。"""

        record = await self.get_by_id(db, inbox_id)
        if record is None or record.id is None:
            return None
        return RuntimeInboxEvidence(
            id=record.id,
            status=record.status,
            event_id=record.event_id,
            attempt_count=record.attempt_count,
            max_retries=record.max_retries,
            next_retry_at=record.next_retry_at,
            processed_at=record.processed_at,
            failed_at=record.failed_at,
            last_error_code=record.last_error_code,
            last_error_message=record.last_error_message,
        )

    async def count_unfinished_by_workline(self, db: AsyncSession, workline_id: int) -> int:
        """按显式 workline_id 统计非终态 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(func.count())
            .select_from(RuntimeInbox)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(RUNTIME_INBOX_UNFINISHED_STATUSES),
            )
        )
        return int(result.scalar_one() or 0)

    async def first_unfinished_by_workline(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> RuntimeInboxWorkloadSample | None:
        """按主键稳定顺序返回首条非终态 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(columns.id, columns.status)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(RUNTIME_INBOX_UNFINISHED_STATUSES),
            )
            .order_by(columns.id.asc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        return RuntimeInboxWorkloadSample(id=int(row[0]), status=str(row[1]))


runtime_inbox_repository = RuntimeInboxRepository()


__all__ = ["RuntimeInboxRepository", "runtime_inbox_repository"]
