"""SystemOutbox repository。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from src.app.handling.models import SystemOutbox, SystemOutboxStatus
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SystemOutboxRepository(BaseRepository[SystemOutbox]):
    """系统级发件箱数据访问。"""

    DISPATCH_LEASE_SECONDS = 300

    def __init__(self) -> None:
        super().__init__(SystemOutbox)

    async def get_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> SystemOutbox | None:
        result = await db.execute(select(SystemOutbox).where(SystemOutbox.dispatch_key == dispatch_key))
        return cast("SystemOutbox | None", result.scalar_one_or_none())

    async def get_pending_messages(self, db: AsyncSession, limit: int = 50) -> list[SystemOutbox]:
        now = timezone.now_for_db()
        result = await db.execute(
            select(SystemOutbox)
            .where(
                (
                    (SystemOutbox.status == SystemOutboxStatus.NEW)
                    & ((SystemOutbox.next_retry_at.is_(None)) | (SystemOutbox.next_retry_at <= now))
                )
                | (
                    (SystemOutbox.status == SystemOutboxStatus.DISPATCHING)
                    & (SystemOutbox.next_retry_at.is_not(None))
                    & (SystemOutbox.next_retry_at <= now)
                ),
            )
            .order_by(SystemOutbox.created_at.asc(), SystemOutbox.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_as_dispatching(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        result = await db.execute(select(SystemOutbox).where(SystemOutbox.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        now = timezone.now_for_db()
        stale_dispatching = (
            outbox.status == SystemOutboxStatus.DISPATCHING
            and outbox.next_retry_at is not None
            and outbox.next_retry_at <= now
        )
        if outbox.status != SystemOutboxStatus.NEW and not stale_dispatching:
            return None
        outbox.status = SystemOutboxStatus.DISPATCHING
        # next_retry_at doubles as a dispatch lease deadline while status is DISPATCHING.
        outbox.next_retry_at = now + timedelta(seconds=self.DISPATCH_LEASE_SECONDS)
        await db.flush()
        return outbox

    async def mark_as_sent(self, db: AsyncSession, outbox_id: int) -> SystemOutbox | None:
        result = await db.execute(select(SystemOutbox).where(SystemOutbox.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status != SystemOutboxStatus.DISPATCHING:
            return None
        outbox.status = SystemOutboxStatus.SENT
        outbox.sent_at = timezone.now_for_db()
        outbox.next_retry_at = None
        outbox.last_error = None
        await db.flush()
        return outbox

    async def mark_as_failed(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        max_retries: int = 3,
    ) -> SystemOutbox | None:
        result = await db.execute(select(SystemOutbox).where(SystemOutbox.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()
        if outbox is None:
            return None
        if outbox.status not in {SystemOutboxStatus.NEW, SystemOutboxStatus.DISPATCHING}:
            return None

        outbox.attempt_count += 1
        outbox.last_error = error
        if outbox.attempt_count > max_retries:
            outbox.status = SystemOutboxStatus.FAILED
            outbox.next_retry_at = None
            outbox.finished_at = timezone.now_for_db()
        else:
            outbox.status = SystemOutboxStatus.NEW
            outbox.next_retry_at = timezone.now_for_db() + timedelta(seconds=2 ** (outbox.attempt_count - 1))
        await db.flush()
        return outbox


system_outbox_repository = SystemOutboxRepository()


__all__ = ["SystemOutboxRepository", "system_outbox_repository"]
