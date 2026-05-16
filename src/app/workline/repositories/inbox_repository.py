"""WorklineInbox Repository 层"""

import hashlib
from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.inbox import (
    InboxKind,
    InboxStatus,
    WorklineInbox,
)
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone


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
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox).where(
                columns.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_idempotent(
        self,
        db: AsyncSession,
        data: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> WorklineInbox:
        """按 idempotency_key 原子创建；冲突时返回已有记录，不回滚当前事务。"""

        table = cast("Any", WorklineInbox).__table__
        statement = (
            insert(table)
            .values(**data)
            .on_conflict_do_nothing(
                index_elements=["idempotency_key"],
                index_where=table.c.idempotency_key.is_not(None),
            )
            .returning(table.c.id)
        )
        result = await db.execute(statement)
        created_id = result.scalar_one_or_none()
        if isinstance(created_id, int):
            created = await self.get_by_id(db, created_id)
            if created is None:
                raise RuntimeError(f"创建 Inbox 后无法读取: id={created_id}")
            return created

        existing = await self.get_by_idempotency_key(db, idempotency_key)
        if existing is None:
            raise RuntimeError(f"Inbox 幂等创建冲突后无法读取原消息: {idempotency_key}")
        return existing

    async def get_new_messages(
        self,
        db: AsyncSession,
        limit: int = 10,
    ) -> list[WorklineInbox]:
        """获取待处理的新消息

        包括：
        - NEW 状态的消息
        - RETRY 状态且 next_retry_at <= now 的消息（重试到期）
        """
        columns = cast("Any", WorklineInbox).__table__.c
        now = timezone.now_for_db()

        # NEW 状态的消息，或 RETRY 状态且重试时间已到的消息
        retry_ready = and_(
            columns.status == InboxStatus.RETRY,
            columns.next_retry_at <= now,
        )

        result = await db.execute(
            select(WorklineInbox)
            .where(
                or_(
                    columns.status == InboxStatus.NEW,
                    retry_ready,
                )
            )
            .order_by(columns.received_at)
            .limit(limit)
            .with_for_update(skip_locked=True)  # 加锁，避免并发消费
        )
        return list(result.scalars().all())

    async def get_by_kind(
        self,
        db: AsyncSession,
        kind: InboxKind,
        limit: int = 100,
    ) -> list[WorklineInbox]:
        """根据消息类型查询"""
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox).where(columns.kind == kind).order_by(columns.received_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    def calculate_device_event_idempotency_key(
        self,
        device_code: str,
        event_type: str,
        timestamp: int,
        data: dict[str, Any],
    ) -> str:
        """
        计算设备事件的幂等键

        规则（白皮书 6.3.1）：
        - 优先使用厂商事件 ID
        - 若无，则 device_code + event_type + timestamp + payload_hash
        """
        # 尝试从 data 中获取厂商事件 ID
        vendor_event_id = cast("str | None", data.get("event_id") or data.get("vendor_event_id"))
        if vendor_event_id:
            return f"device_event:{vendor_event_id}"

        # 计算 payload_hash
        payload_items: list[tuple[str, Any]] = sorted(data.items())
        payload_str = str(payload_items)  # 确保字典顺序一致
        payload_hash = hashlib.md5(payload_str.encode(), usedforsecurity=False).hexdigest()[:8]

        # 组合键
        return f"device_event:{device_code}:{event_type}:{timestamp}:{payload_hash}"

    def calculate_command_result_idempotency_key(
        self,
        command_code: str,
        result: str,
        finish_time: int,
        data: dict[str, Any],
    ) -> str:
        """
        计算指令结果的幂等键

        规则（白皮书 6.3.1）：
        - command_code + result + finish_time + payload_hash
        """
        # 计算 payload_hash
        payload_items: list[tuple[str, Any]] = sorted(data.items())
        payload_str = str(payload_items)  # 确保字典顺序一致
        payload_hash = hashlib.md5(payload_str.encode(), usedforsecurity=False).hexdigest()[:8]

        # 组合键
        return f"command_result:{command_code}:{result}:{finish_time}:{payload_hash}"

    def calculate_external_http_idempotency_key(
        self,
        callback_type: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str:
        """计算外部 HTTP 回调的幂等键。"""

        source_event_id = payload.get("source_event_id")
        if not isinstance(source_event_id, str) or not source_event_id.strip():
            data = payload.get("data")
            if isinstance(data, dict):
                source_event_id = data.get("source_event_id")
        if isinstance(source_event_id, str) and source_event_id.strip():
            return f"external_http:{callback_type}:{trace_id}:source_event:{source_event_id.strip()}"

        payload_items: list[tuple[str, Any]] = sorted(payload.items())
        payload_str = str(payload_items)
        payload_hash = hashlib.md5(payload_str.encode(), usedforsecurity=False).hexdigest()[:8]
        return f"external_http:{callback_type}:{trace_id}:{payload_hash}"


# 创建单例
inbox_repository = WorklineInboxRepository()
