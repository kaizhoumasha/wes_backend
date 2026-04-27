"""WorklineInbox Service 层"""

from __future__ import annotations

from datetime import timedelta
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
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        canonical_event_type: str | None = None,
        *,
        auto_commit: bool = True,
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
            trace_id: Trace ID（可选）
            auto_commit: 是否在创建后立即提交。批处理/编排场景应显式传 False。

        Returns:
            创建的 Inbox 消息

        Raises:
            ValueError: 如果消息已存在（幂等检查失败）
        """
        idempotency_key = (
            f"device_event:{event_id}"
            if event_id
            else self.repo.calculate_device_event_idempotency_key(
                device_code=device_code,
                event_type=event_type,
                timestamp=timestamp,
                data=data,
            )
        )
        payload: dict[str, Any] = {
            "message_type": "DEVICE_EVENT",
            "device_code": device_code,
            "event_type": event_type,
            "canonical_event_type": canonical_event_type or event_type,
            "timestamp": timestamp,
            "data": data,
        }
        if event_id:
            payload["event_id"] = event_id
        if causation_id:
            payload["causation_id"] = causation_id

        return await self._create_inbox_message(
            db=db,
            idempotency_key=idempotency_key,
            duplicate_message="设备事件已存在（幂等键重复）",
            kind=InboxKind.DEVICE_EVENT,
            payload=payload,
            source_message_id=source_message_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            auto_commit=auto_commit,
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
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        *,
        auto_commit: bool = True,
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
            trace_id: Trace ID（可选）
            auto_commit: 是否在创建后立即提交。批处理/编排场景应显式传 False。

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
        if event_id:
            payload["event_id"] = event_id
        if causation_id:
            payload["causation_id"] = causation_id

        return await self._create_inbox_message(
            db=db,
            idempotency_key=idempotency_key,
            duplicate_message="指令结果已存在（幂等键重复）",
            kind=InboxKind.COMMAND_RESULT,
            payload=payload,
            source_message_id=source_message_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            auto_commit=auto_commit,
        )

    async def create_external_http_inbox(
        self,
        db: AsyncSession,
        *,
        callback_type: str,
        trace_id: str,
        payload: dict[str, Any],
        source_system: SourceSystem = SourceSystem.SYSTEM,
        source_message_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        auto_commit: bool = True,
    ) -> WorklineInbox:
        """创建外部 HTTP 回调 Inbox 消息。"""

        idempotency_key = self.repo.calculate_external_http_idempotency_key(
            callback_type=callback_type,
            trace_id=trace_id,
            payload=payload,
        )

        inbox_payload: dict[str, Any] = {
            **payload,
            "message_type": "EXTERNAL_HTTP",
            "callback_type": callback_type,
        }

        return await self._create_inbox_message(
            db=db,
            idempotency_key=idempotency_key,
            duplicate_message="外部 HTTP 回调已存在（幂等键重复）",
            kind=InboxKind.EXTERNAL_HTTP,
            payload=inbox_payload,
            source_message_id=source_message_id,
            trace_id=trace_id,
            event_id=event_id,
            causation_id=causation_id,
            source_system=source_system,
            auto_commit=auto_commit,
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
        *,
        auto_commit: bool = True,
    ) -> WorklineInbox:
        """
        标记消息为处理中

        Args:
            db: 数据库会话
            inbox_id: 消息 ID
            processor_token: 处理器令牌
            auto_commit: 是否在更新后立即提交。批处理场景应显式传 False。

        Returns:
            更新后的消息
        """
        return await self._update_inbox(
            db,
            inbox_id,
            status=InboxStatus.PROCESSING,
            processor_token=processor_token,
            auto_commit=auto_commit,
        )

    async def mark_as_processed(
        self,
        db: AsyncSession,
        inbox_id: int,
        *,
        auto_commit: bool = True,
    ) -> WorklineInbox:
        """
        标记消息为已处理

        Args:
            db: 数据库会话
            inbox_id: 消息 ID
            auto_commit: 是否在更新后立即提交。批处理场景应显式传 False。

        Returns:
            更新后的消息
        """
        return await self._update_inbox(
            db,
            inbox_id,
            status=InboxStatus.PROCESSED,
            processed_at=timezone.now_for_db(),
            auto_commit=auto_commit,
        )

    async def mark_as_failed(
        self,
        db: AsyncSession,
        inbox_id: int,
        error_message: str,
        *,
        auto_commit: bool = True,
    ) -> WorklineInbox:
        """
        标记消息处理失败

        支持重试机制：
        - 如果 attempt_count < max_attempts，设置 next_retry_at，状态设为 RETRY
        - 如果 attempt_count >= max_attempts，状态设为 DEAD_LETTER

        Args:
            db: 数据库会话
            inbox_id: 消息 ID
            error_message: 错误消息
            auto_commit: 是否在更新后立即提交。批处理场景应显式传 False。

        Returns:
            更新后的消息
        """
        # 获取当前 inbox 以检查重试次数
        inbox = await self.repo.get_by_id(db, inbox_id)
        if inbox is None:
            raise ValueError(f"Inbox {inbox_id} not found")

        attempt_count = getattr(inbox, "attempt_count", 0) or 0
        max_attempts = getattr(inbox, "max_attempts", 3) or 3

        if attempt_count < max_attempts:
            # 可以重试：增加计数，设置下次重试时间
            next_retry = timezone.now_for_db() + timedelta(seconds=60 * (2**attempt_count))
            return await self._update_inbox(
                db,
                inbox_id,
                status=InboxStatus.RETRY,
                error_message=error_message,
                attempt_count=attempt_count + 1,
                next_retry_at=next_retry,
                processed_at=timezone.now_for_db(),
                auto_commit=auto_commit,
            )

        # 重试耗尽：进入死信队列
        return await self._update_inbox(
            db,
            inbox_id,
            status=InboxStatus.DEAD_LETTER,
            error_message=error_message,
            processed_at=timezone.now_for_db(),
            auto_commit=auto_commit,
        )

    async def _create_inbox_message(
        self,
        db: AsyncSession,
        idempotency_key: str,
        duplicate_message: str,
        kind: InboxKind,
        payload: dict[str, Any],
        source_message_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        source_system: SourceSystem = SourceSystem.DEVICE,
        *,
        auto_commit: bool = True,
    ) -> WorklineInbox:
        existing = await self.repo.get_by_idempotency_key(db, idempotency_key)
        if existing:
            raise ValueError(f"{duplicate_message}: {idempotency_key}, 原消息 ID: {existing.id}")

        inbox_data: dict[str, Any] = {
            "kind": kind,
            "idempotency_key": idempotency_key,
            "source_system": source_system,
            "source_message_id": source_message_id,
            "payload_json": payload,
            "status": InboxStatus.NEW,
            "received_at": timezone.now_for_db(),
        }

        if trace_id:
            inbox_data["trace_id"] = trace_id
        if event_id:
            inbox_data["event_id"] = event_id
        if causation_id:
            inbox_data["causation_id"] = causation_id

        created = await self.repo.create(db, inbox_data)
        if created is None:
            raise RuntimeError("创建 Inbox 消息失败")
        await self._commit_inbox_mutation(db, auto_commit=auto_commit)
        return created

    async def _update_inbox(
        self,
        db: AsyncSession,
        inbox_id: int,
        *,
        auto_commit: bool = True,
        **data: Any,
    ) -> WorklineInbox:
        inbox = await self.repo.get_by_id(db, inbox_id)
        if not inbox:
            raise ValueError(f"消息不存在: {inbox_id}")

        updated = await self.repo.update(db, inbox_id, data)
        if updated is None:
            raise RuntimeError(f"更新 Inbox 消息失败: {inbox_id}")
        await self._commit_inbox_mutation(db, auto_commit=auto_commit)
        return updated

    async def create_timeout_inbox(
        self,
        db: AsyncSession,
        session_id: int,
        workline_id: int,
        deadline_at: object | None = None,
        trace_id: str | None = None,
        *,
        auto_commit: bool = True,
    ) -> WorklineInbox:
        """
        创建超时 Inbox 消息

        Args:
            db: 数据库会话
            session_id: 会话 ID
            workline_id: 作业线 ID
            trace_id: Trace ID（可选）
            auto_commit: 是否在创建后立即提交。批处理场景应显式传 False。

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

        if trace_id:
            inbox_data["trace_id"] = trace_id

        created = await self.repo.create(db, inbox_data)
        if created is None:
            raise RuntimeError("创建超时 Inbox 消息失败")
        await self._commit_inbox_mutation(db, auto_commit=auto_commit)
        return created

    async def _commit_inbox_mutation(self, db: AsyncSession, *, auto_commit: bool) -> None:
        if not auto_commit:
            return
        await self._commit_mutation(db)


# 创建单例
inbox_service: WorklineInboxService = WorklineInboxService()
