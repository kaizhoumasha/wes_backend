"""WorklineInbox Repository 层"""

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.inbox import (
    InboxKind,
    InboxStatus,
    WorklineInbox,
)
from src.database.base_repository import BaseRepository


class WorklineInboxRepository(BaseRepository[WorklineInbox]):
    """作业线收件箱数据访问层"""

    def __init__(self) -> None:
        """初始化收件箱仓库"""
        super().__init__(WorklineInbox)

    async def get_by_idempotency_key(
        self,
        db: AsyncSession,
        idempotency_key: str,
    ) -> WorklineInbox | None:
        """根据幂等键查询（用于幂等检查）"""
        result = await db.execute(
            select(WorklineInbox).where(
                WorklineInbox.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_new_messages(
        self,
        db: AsyncSession,
        limit: int = 10,
    ) -> list[WorklineInbox]:
        """获取待处理的新消息"""
        result = await db.execute(
            select(WorklineInbox)
            .where(WorklineInbox.status == InboxStatus.NEW)
            .order_by(WorklineInbox.received_at)
            .limit(limit)
            .with_for_update()  # 加锁，避免并发消费
        )
        return list(result.scalars().all())

    async def get_by_kind(
        self,
        db: AsyncSession,
        kind: InboxKind,
        limit: int = 100,
    ) -> list[WorklineInbox]:
        """根据消息类型查询"""
        result = await db.execute(
            select(WorklineInbox)
            .where(WorklineInbox.kind == kind)
            .order_by(WorklineInbox.received_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    def calculate_device_event_idempotency_key(
        self,
        device_code: str,
        event_type: str,
        timestamp: int,
        data: dict,
    ) -> str:
        """
        计算设备事件的幂等键

        规则（白皮书 6.3.1）：
        - 优先使用厂商事件 ID
        - 若无，则 device_code + event_type + timestamp + payload_hash
        """
        # 尝试从 data 中获取厂商事件 ID
        vendor_event_id = data.get("event_id") or data.get("vendor_event_id")
        if vendor_event_id:
            return f"device_event:{vendor_event_id}"

        # 计算 payload_hash
        payload_str = str(sorted(data.items()))  # 确保字典顺序一致
        payload_hash = hashlib.md5(payload_str.encode()).hexdigest()[:8]  # noqa: S324

        # 组合键
        return f"device_event:{device_code}:{event_type}:{timestamp}:{payload_hash}"

    def calculate_command_result_idempotency_key(
        self,
        command_code: str,
        result: str,
        finish_time: int,
        data: dict,
    ) -> str:
        """
        计算指令结果的幂等键

        规则（白皮书 6.3.1）：
        - command_code + result + finish_time + payload_hash
        """
        # 计算 payload_hash
        payload_str = str(sorted(data.items()))  # 确保字典顺序一致
        payload_hash = hashlib.md5(payload_str.encode()).hexdigest()[:8]  # noqa: S324

        # 组合键
        return f"command_result:{command_code}:{result}:{finish_time}:{payload_hash}"


# 创建单例
inbox_repository = WorklineInboxRepository()
