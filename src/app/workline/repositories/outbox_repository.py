"""WorklineOutbox Repository 层"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.outbox import OutboxStatus, WorklineOutbox
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone


class WorklineOutboxRepository(BaseRepository[WorklineOutbox]):
    """作业线发件箱数据访问层"""

    def __init__(self) -> None:
        """初始化发件箱仓库"""
        super().__init__(WorklineOutbox)

    async def get_pending_messages(
        self,
        db: AsyncSession,
        limit: int = 50,
    ) -> list[WorklineOutbox]:
        """获取待派发的消息

        只查询 NEW 状态且未到重试时间或重试时间已过的消息。

        Args:
            db: 数据库会话
            limit: 最大返回数量

        Returns:
            待派发的消息列表
        """
        columns = cast("Any", WorklineOutbox).__table__.c
        now = timezone.now_for_db()

        result = await db.execute(
            select(WorklineOutbox)
            .where(
                columns.status == OutboxStatus.NEW,
                # next_retry_at 为空或已过重试时间
                (columns.next_retry_at.is_(None)) | (columns.next_retry_at <= now),
            )
            .order_by(columns.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def mark_as_dispatching(
        self,
        db: AsyncSession,
        outbox_id: int,
    ) -> WorklineOutbox | None:
        """标记消息为派发中

        原子更新，用于并发控制。

        Args:
            db: 数据库会话
            outbox_id: 消息 ID

        Returns:
            更新后的消息，如果已被其他进程处理则返回 None
        """
        columns = cast("Any", WorklineOutbox).__table__.c

        # 先检查当前状态
        result = await db.execute(select(WorklineOutbox).where(columns.id == outbox_id).with_for_update())
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        if outbox.status != OutboxStatus.NEW:
            # 已被其他进程处理
            return None

        # 更新状态
        outbox.status = OutboxStatus.DISPATCHING
        await db.flush()
        return outbox

    async def mark_as_sent(
        self,
        db: AsyncSession,
        outbox_id: int,
    ) -> WorklineOutbox | None:
        """标记消息为已发送

        Args:
            db: 数据库会话
            outbox_id: 消息 ID

        Returns:
            更新后的消息
        """
        result = await db.execute(select(WorklineOutbox).where(cast("Any", WorklineOutbox).__table__.c.id == outbox_id))
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        outbox.status = OutboxStatus.SENT
        outbox.sent_at = timezone.now_for_db()
        await db.flush()
        return outbox

    async def mark_as_failed(
        self,
        db: AsyncSession,
        outbox_id: int,
        error: str,
        max_retries: int = 3,
    ) -> WorklineOutbox | None:
        """标记消息为失败，设置重试或永久失败

        Args:
            db: 数据库会话
            outbox_id: 消息 ID
            error: 错误信息
            max_retries: 最大重试次数

        Returns:
            更新后的消息
        """
        from datetime import timedelta

        result = await db.execute(select(WorklineOutbox).where(cast("Any", WorklineOutbox).__table__.c.id == outbox_id))
        outbox = result.scalar_one_or_none()

        if not outbox:
            return None

        outbox.attempt_count += 1
        outbox.last_error = error

        if outbox.attempt_count >= max_retries:
            # 达到最大重试次数，标记为失败
            outbox.status = OutboxStatus.FAILED
            outbox.finished_at = timezone.now_for_db()
        else:
            # 设置重试时间（指数退避）
            retry_delay = timedelta(seconds=30 * (2**outbox.attempt_count))
            outbox.next_retry_at = timezone.now_for_db() + retry_delay
            outbox.status = OutboxStatus.NEW  # 重置为 NEW 以便重试

        await db.flush()
        return outbox


# 创建单例
outbox_repository = WorklineOutboxRepository()
