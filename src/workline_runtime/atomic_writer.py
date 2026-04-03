"""
原子写入器 - 单一事务内更新所有实体

本模块提供原子写入能力，确保 Session、Timeline、Outbox、Inbox 的更新在同一事务中完成。

设计原则：
- DRY: 统一的原子写入逻辑，避免分散的事务管理
- KISS: 简单的事务管理，不引入复杂的补偿机制
- SOLID: 单一职责，只负责原子写入

写入顺序（保证追溯完整性）：
1. Session 更新（状态、上下文）
2. Timeline 记录（seq_no 从数据库序列获取）
3. DecisionLog 记录（如有）
4. Outbox 记录（如有）
5. Inbox 状态更新

相关文档:
- 白皮书: @docs/workline_plugin_architecture_design.md
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.inbox import InboxStatus
from src.utils.timezone import timezone


class AtomicWriter:
    """
    原子写入器 - 单一事务内更新所有实体

    提供原子写入能力，确保所有实体的更新在同一事务中完成。
    如果任何操作失败，整个事务将回滚。

    用法:
        writer = AtomicWriter()
        await writer.commit(
            db=db_session,
            session=session,
            timelines=[timeline1, timeline2],
            outboxes=[outbox1],
            inbox=inbox,
        )
    """

    async def commit(
        self,
        db: AsyncSession,
        session: Any,  # WorklineSession
        timelines: list[Any],  # list[WorklineTimeline]
        outboxes: list[Any] | None,  # list[WorklineOutbox] | None
        inbox: Any,  # WorklineInbox
    ) -> None:
        """
        在单一事务中原子更新所有实体

        写入顺序（保证追溯完整性）：
        1. Session 更新（状态、上下文）
        2. Timeline 记录（seq_no 从数据库序列获取）
        3. Outbox 记录（如有）
        4. Inbox 状态更新

        Args:
            db: 数据库会话
            session: WorklineSession 实例
            timelines: WorklineTimeline 实例列表
            outboxes: WorklineOutbox 实例列表或 None
            inbox: WorklineInbox 实例

        Raises:
            Exception: 任何数据库操作失败时抛出异常，事务将回滚
        """
        try:
            _ = session
            # 1. Session 已在内存中更新，无需额外操作
            # Session 的状态和上下文已经在上层逻辑中更新

            # 2. 插入 Timeline 记录（为每个 Timeline 获取 seq_no）
            for timeline in timelines:
                seq_no = await self._get_next_seq_no(db)
                timeline.seq_no = seq_no
                db.add(timeline)

            # 3. 插入 Outbox 记录（如有）
            if outboxes:
                for outbox in outboxes:
                    db.add(outbox)

            # 4. 更新 Inbox 状态
            inbox.status = InboxStatus.PROCESSED
            inbox.processed_at = timezone.now_for_db()

        except Exception:
            # 回滚事务
            await db.rollback()
            raise

    async def _get_next_seq_no(self, db: AsyncSession) -> int:
        """
        从数据库序列获取下一个 seq_no

        使用 PostgreSQL 序列确保 seq_no 的全局单调递增。

        Args:
            db: 数据库会话

        Returns:
            下一个 seq_no 值
        """
        result = await db.execute(text("SELECT nextval('wes_biz.workline_timeline_seq_no_seq')"))
        seq_no = result.scalar()
        if isinstance(seq_no, bool) or not isinstance(seq_no, int):
            raise TypeError("Failed to fetch next timeline sequence number")
        return seq_no


# 导出单例
atomic_writer = AtomicWriter()

__all__ = ["AtomicWriter", "atomic_writer"]
