"""RuntimeInbox repository for ACK/idempotency/replay consumer flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RuntimeInboxRepository(BaseRepository[RuntimeInbox]):
    """RuntimeInbox 数据访问层。"""

    def __init__(self) -> None:
        super().__init__(RuntimeInbox)

    async def get_by_source_event_identity(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str,
    ) -> RuntimeInbox | None:
        """按入站事件幂等身份读取 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(RuntimeInbox).where(
                columns.provider_code == provider_code,
                columns.event_type == event_type,
                columns.source_event_id == source_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, db: AsyncSession, inbox_id: int) -> RuntimeInbox | None:
        """锁定读取单条 RuntimeInbox，用于人工重放。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(select(RuntimeInbox).where(columns.id == inbox_id).with_for_update())
        return result.scalar_one_or_none()

    async def add_received(self, db: AsyncSession, data: dict[str, Any]) -> RuntimeInbox:
        """新建 RECEIVED RuntimeInbox 并 flush，调用方控制事务提交。"""

        record = RuntimeInbox(**data)
        db.add(record)
        await db.flush()
        await db.refresh(record)
        return record


runtime_inbox_repository = RuntimeInboxRepository()


__all__ = ["RuntimeInboxRepository", "runtime_inbox_repository"]
