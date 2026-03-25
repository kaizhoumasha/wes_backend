"""WorklineInbox Service 层"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.app.workline.models.inbox import (
    InboxKind,
    InboxStatus,
    SourceSystem,
    WorklineInbox,
)
from src.app.workline.repositories import inbox_repository
from src.core.base_service import BaseService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class _SupportsIsoformat(Protocol):
    def isoformat(self) -> str: ...


def _format_deadline(deadline_at: object | None) -> str:
    if deadline_at is None or not isinstance(deadline_at, _SupportsIsoformat):
        return "unknown"

    return deadline_at.isoformat()


class WorklineInboxService(BaseService[WorklineInbox, type(inbox_repository)]):
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
    ) -> WorklineInbox:
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
        idempotency_key = self.repo.calculate_device_event_idempotency_key(
            device_code=device_code,
            event_type=event_type,
            timestamp=timestamp,
            data=data,
        )
        payload: dict[str, Any] = {
            "message_type": "DEVICE_EVENT",
            "device_code": device_code,
            "event_type": event_type,
            "timestamp": timestamp,
            "data": data,
        }

        return await self._create_inbox_message(
            db=db,
            idempotency_key=idempotency_key,
            duplicate_message="设备事件已存在（幂等键重复）",
            kind=InboxKind.DEVICE_EVENT,
            payload=payload,
            source_message_id=source_message_id,
            correlation_id=correlation_id,
        )

    async def create_command_result_inbox(
        self,
        db: AsyncSession,
        command_code: str,
        device_code: str,
        result: str,
        finish_time: int,
        data: dict[str, Any] | None = None,
        command_type: str | None = None,
        error_detail: dict[str, Any] | None = None,
        source_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> WorklineInbox:
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
        payload_data = data or {}
        idempotency_key = self.repo.calculate_command_result_idempotency_key(
            command_code=command_code,
            result=result,
            finish_time=finish_time,
            data=payload_data,
        )
        payload: dict[str, Any] = {
            "message_type": "COMMAND_RESULT",
            "command_code": command_code,
            "device_code": device_code,
            "result": result,
            "finish_time": finish_time,
            "data": payload_data,
        }
        if command_type:
            payload["command_type"] = command_type
        if error_detail:
            payload["error_detail"] = error_detail

        return await self._create_inbox_message(
            db=db,
            idempotency_key=idempotency_key,
            duplicate_message="指令结果已存在（幂等键重复）",
            kind=InboxKind.COMMAND_RESULT,
            payload=payload,
            source_message_id=source_message_id,
            correlation_id=correlation_id,
        )

    async def get_new_messages(
        self,
        db: AsyncSession,
        limit: int = 10,
    ) -> list[WorklineInbox]:
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
    ) -> WorklineInbox:
        """
        标记消息为处理中

        Args:
            db: 数据库会话
            inbox_id: 消息 ID
            processor_token: 处理器令牌

        Returns:
            更新后的消息
        """
        return await self._update_inbox(
            db,
            inbox_id,
            status=InboxStatus.PROCESSING,
            processor_token=processor_token,
        )

    async def mark_as_processed(
        self,
        db: AsyncSession,
        inbox_id: int,
    ) -> WorklineInbox:
        """
        标记消息为已处理

        Args:
            db: 数据库会话
            inbox_id: 消息 ID

        Returns:
            更新后的消息
        """
        return await self._update_inbox(
            db,
            inbox_id,
            status=InboxStatus.PROCESSED,
            processed_at=timezone.now_for_db(),
        )

    async def mark_as_failed(
        self,
        db: AsyncSession,
        inbox_id: int,
        error_message: str,
    ) -> WorklineInbox:
        """
        标记消息处理失败

        Args:
            db: 数据库会话
            inbox_id: 消息 ID
            error_message: 错误消息

        Returns:
            更新后的消息
        """
        return await self._update_inbox(
            db,
            inbox_id,
            status=InboxStatus.FAILED,
            error_message=error_message,
            processed_at=timezone.now_for_db(),
        )

    async def _create_inbox_message(
        self,
        db: AsyncSession,
        idempotency_key: str,
        duplicate_message: str,
        kind: InboxKind,
        payload: dict[str, Any],
        source_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> WorklineInbox:
        existing = await self.repo.get_by_idempotency_key(db, idempotency_key)
        if existing:
            raise ValueError(f"{duplicate_message}: {idempotency_key}, 原消息 ID: {existing.id}")

        inbox_data: dict[str, Any] = {
            "kind": kind,
            "idempotency_key": idempotency_key,
            "source_system": SourceSystem.DEVICE,
            "source_message_id": source_message_id,
            "payload_json": payload,
            "status": InboxStatus.NEW,
            "received_at": timezone.now_for_db(),
        }

        if correlation_id:
            inbox_data["correlation_id"] = correlation_id

        created = await self.repo.create(db, inbox_data)
        if created is None:
            raise RuntimeError("创建 Inbox 消息失败")
        return created

    async def _update_inbox(self, db: AsyncSession, inbox_id: int, **data: Any) -> WorklineInbox:
        inbox = await self.repo.get_by_id(db, inbox_id)
        if not inbox:
            raise ValueError(f"消息不存在: {inbox_id}")

        updated = await self.repo.update(db, inbox_id, data)
        if updated is None:
            raise RuntimeError(f"更新 Inbox 消息失败: {inbox_id}")
        return updated

    async def create_timeout_inbox(
        self,
        db: AsyncSession,
        session_id: int,
        workline_id: int,
        deadline_at: object | None = None,
        correlation_id: str | None = None,
    ) -> WorklineInbox:
        """
        创建超时 Inbox 消息

        Args:
            db: 数据库会话
            session_id: 会话 ID
            workline_id: 作业线 ID
            correlation_id: 关联 ID（可选）

        Returns:
            创建的 Inbox 消息
        """
        timeout_key = _format_deadline(deadline_at)
        idempotency_key = f"timeout:{session_id}:{timeout_key}"
        payload: dict[str, Any] = {
            "message_type": "TIMEOUT",
            "session_id": session_id,
            "workline_id": workline_id,
            "timeout_at": timezone.now_for_db().isoformat(),
            "deadline_at": timeout_key,
        }

        inbox_data: dict[str, Any] = {
            "kind": InboxKind.TIMER_TIMEOUT,
            "idempotency_key": idempotency_key,
            "source_system": SourceSystem.SYSTEM,
            "session_id": session_id,
            "workline_id": workline_id,
            "payload_json": payload,
            "status": InboxStatus.NEW,
            "received_at": timezone.now_for_db(),
        }

        if correlation_id:
            inbox_data["correlation_id"] = correlation_id

        created = await self.repo.create(db, inbox_data)
        if created is None:
            raise RuntimeError("创建超时 Inbox 消息失败")
        return created


# 创建单例
inbox_service: WorklineInboxService = WorklineInboxService()
