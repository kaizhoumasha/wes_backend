"""兼容导出：Workline Outbox Repository 已迁移为 SystemOutboxRepository。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.sys.repositories import SystemOutboxRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.sys.models import SystemOutbox


class WorklineOutboxRepository(SystemOutboxRepository):
    """Workline 兼容仓储，只扫描 Workline 域消息。"""

    async def get_pending_messages(self, db: AsyncSession, limit: int = 50) -> list[SystemOutbox]:
        return await super().get_pending_messages(db, limit=limit, operation_domains=("WORKLINE",))


outbox_repository = WorklineOutboxRepository()

__all__ = ["WorklineOutboxRepository", "outbox_repository"]
