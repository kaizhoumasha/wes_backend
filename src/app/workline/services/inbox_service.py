"""WorklineInbox Service 层"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.inbox import (
    InboxKind,
    InboxStatus,
    SourceSystem,
)
from src.app.workline.repositories import inbox_repository
from src.core.base_service import BaseService
from src.utils.timezone import timezone


class WorklineInboxService(BaseService["WorklineInbox", type(inbox_repository)]):
    """作业线收件箱业务逻辑层"""

    def __init__(self) -> None:
        super().__init__(
            inbox_repository,
            enable_cache=False,  # Inbox 不需要缓存
        )

    async def create_device_event_inbox(
        self,
        db: AsyncSession,
        device_code: str,
        event_type: str,
        timestamp: int,
        data: dict[str, Any],
        source_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """
        创建设备事件 Inbox 消息

        Args:
            db: 数据库会话
            device_code: 设备编码
            event_type: 事件类型
            timestamp: 时间戳（毫秒）
            data: 事件数据
            source_message_id: 来源消息 ID（可选）
            correlation_id: 关联 ID（可选）

        Returns:
            创建的 Inbox 消息

        Raises:
            ValueError: 如果消息已存在（幂等检查失败）
        """
        # 计算幂等键
        idempotency_key = self.repo.calculate_device_event_idempotency_key(
            device_code=device_code,
            event_type=event_type,
            timestamp=timestamp,
            data=data,
        )

        # 幂等检查
        existing = await self.repo.get_by_idempotency_key(db, idempotency_key)
        if existing:
            raise ValueError(f"设备事件已存在（幂等键重复）: {idempotency_key}, 原消息 ID: {existing.id}")

        # 创建 Inbox 消息
        inbox_data = {
            "kind": InboxKind.DEVICE_EVENT,
            "idempotency_key": idempotency_key,
            "source_system": SourceSystem.DEVICE,
            "source_message_id": source_message_id,
            "payload_json": {
                "device_code": device_code,
                "event_type": event_type,
                "timestamp": timestamp,
                "data": data,
            },
            "status": InboxStatus.NEW,
            "received_at": timezone.now_for_db(),
        }

        if correlation_id:
            inbox_data["correlation_id"] = correlation_id

        return await self.repo.create(db, inbox_data)

    async def create_command_result_inbox(
        self,
        db: AsyncSession,
        command_code: str,
        device_code: str,
        result: str,
        finish_time: int,
        data: dict[str, Any] | None = None,
        source_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """
        创建指令结果 Inbox 消息

        Args:
            db: 数据库会话
            command_code: 指令编码
            device_code: 设备编码
            result: 执行结果
            finish_time: 完成时间（毫秒）
            data: 结果数据
            source_message_id: 来源消息 ID（可选）
            correlation_id: 关联 ID（可选）

        Returns:
            创建的 Inbox 消息

        Raises:
            ValueError: 如果消息已存在（幂等检查失败）
        """
        # 计算幂等键
        idempotency_key = self.repo.calculate_command_result_idempotency_key(
            command_code=command_code,
            result=result,
            finish_time=finish_time,
            data=data or {},
        )

        # 幂等检查
        existing = await self.repo.get_by_idempotency_key(db, idempotency_key)
        if existing:
            raise ValueError(f"指令结果已存在（幂等键重复）: {idempotency_key}, 原消息 ID: {existing.id}")

        # 创建 Inbox 消息
        inbox_data = {
            "kind": InboxKind.DEVICE_EVENT,  # 结果也是事件的一种
            "idempotency_key": idempotency_key,
            "source_system": SourceSystem.DEVICE,
            "source_message_id": source_message_id,
            "payload_json": {
                "command_code": command_code,
                "device_code": device_code,
                "result": result,
                "finish_time": finish_time,
                "data": data or {},
            },
            "status": InboxStatus.NEW,
            "received_at": timezone.now_for_db(),
        }

        if correlation_id:
            inbox_data["correlation_id"] = correlation_id

        return await self.repo.create(db, inbox_data)

    async def get_new_messages(
        self,
        db: AsyncSession,
        limit: int = 10,
    ) -> list[Any]:
        """
        获取待处理的新消息

        Args:
            db: 数据库会话
            limit: 获取数量

        Returns:
            待处理的消息列表
        """
        return await self.repo.get_new_messages(db, limit=limit)

    async def mark_as_processing(
        self,
        db: AsyncSession,
        inbox_id: int,
        processor_token: str,
    ) -> Any:
        """
        标记消息为处理中

        Args:
            db: 数据库会话
            inbox_id: 消息 ID
            processor_token: 处理器令牌

        Returns:
            更新后的消息
        """
        inbox = await self.repo.get_by_id(db, inbox_id)
        if not inbox:
            raise ValueError(f"消息不存在: {inbox_id}")

        inbox.status = InboxStatus.PROCESSING
        inbox.processor_token = processor_token

        return await self.repo.update(db, inbox)

    async def mark_as_processed(
        self,
        db: AsyncSession,
        inbox_id: int,
    ) -> Any:
        """
        标记消息为已处理

        Args:
            db: 数据库会话
            inbox_id: 消息 ID

        Returns:
            更新后的消息
        """
        inbox = await self.repo.get_by_id(db, inbox_id)
        if not inbox:
            raise ValueError(f"消息不存在: {inbox_id}")

        inbox.status = InboxStatus.PROCESSED
        inbox.processed_at = timezone.now_for_db()

        return await self.repo.update(db, inbox)

    async def mark_as_failed(
        self,
        db: AsyncSession,
        inbox_id: int,
        error_message: str,
    ) -> Any:
        """
        标记消息处理失败

        Args:
            db: 数据库会话
            inbox_id: 消息 ID
            error_message: 错误消息

        Returns:
            更新后的消息
        """
        inbox = await self.repo.get_by_id(db, inbox_id)
        if not inbox:
            raise ValueError(f"消息不存在: {inbox_id}")

        inbox.status = InboxStatus.FAILED
        inbox.error_message = error_message
        inbox.processed_at = timezone.now_for_db()

        return await self.repo.update(db, inbox)


# 创建单例
inbox_service = WorklineInboxService()
