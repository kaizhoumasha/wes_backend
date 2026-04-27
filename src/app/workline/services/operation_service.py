"""工作线调试、重放和人工操作 Service。"""

from __future__ import annotations

import uuid
from typing import Any

from src.app.workline.models.inbox import InboxKind, SourceSystem
from src.app.workline.models.session import SessionStatus
from src.app.workline.repositories import inbox_repository, outbox_repository, workline_session_repository
from src.app.workline.repositories.inbox_repository import WorklineInboxRepository  # noqa: TC001
from src.app.workline.repositories.outbox_repository import WorklineOutboxRepository  # noqa: TC001
from src.app.workline.repositories.session_repository import WorklineSessionRepository  # noqa: TC001
from src.core.base_service import BaseService
from src.utils.timezone import timezone
from src.workline_runtime.trace_context import TraceContext

_OPEN_SESSION_STATUSES = {
    SessionStatus.NEW,
    SessionStatus.RUNNING,
    SessionStatus.WAITING_DEVICE_RESULT,
    SessionStatus.WAITING_EXTERNAL,
    SessionStatus.MANUAL_HOLD,
    "NEW",
    "RUNNING",
    "WAITING_DEVICE_RESULT",
    "WAITING_EXTERNAL",
    "MANUAL_HOLD",
}

_MANUAL_OPERATION_KIND = {
    "HOLD": InboxKind.MANUAL_HOLD,
    "RESUME": InboxKind.MANUAL_RESUME,
    "CANCEL": InboxKind.MANUAL_CANCEL,
}


class WorklineOperationService(BaseService[Any, Any]):
    """封装 sandbox / replay / manual 的状态前置条件。"""

    def __init__(
        self,
        *,
        inbox_repo: WorklineInboxRepository | None = None,
        session_repo: WorklineSessionRepository | None = None,
        outbox_repo: WorklineOutboxRepository | None = None,
    ) -> None:
        super().__init__(inbox_repo or inbox_repository, enable_cache=False)
        self.inbox_repo = inbox_repo or inbox_repository
        self.session_repo = session_repo or workline_session_repository
        self.outbox_repo = outbox_repo or outbox_repository

    async def get_sandbox_pending(self, db: Any, *, limit: int = 50) -> list[Any]:
        """查询 SIMULATION 模式下等待调试人员处理的 outbox。"""

        return await self.outbox_repo.get_sandbox_pending_messages(db, limit=limit)

    async def replay_inbox(
        self,
        db: Any,
        *,
        inbox_id: int,
        reason: str,
        operator_id: str | None = None,
        auto_commit: bool = True,
    ) -> Any:
        """从历史 inbox 创建一条新的 replay 请求，不修改原 inbox。"""

        _ = reason, operator_id
        original = await self.inbox_repo.get_by_id(db, inbox_id)
        if original is None:
            raise ValueError(f"Inbox 不存在: {inbox_id}")

        original_payload = getattr(original, "payload_json", None)
        payload = dict(original_payload) if isinstance(original_payload, dict) else {}
        original_event_id = getattr(original, "event_id", None)
        replay_event_id = f"replay:{original_event_id or inbox_id}:{uuid.uuid4().hex}"
        payload.update(
            {
                "replay_of_event_id": original_event_id,
                "replay_reason": reason,
                "replay_operator_id": operator_id,
            }
        )
        replay = await self.inbox_repo.create(
            db,
            {
                "kind": getattr(original, "kind", InboxKind.REPLAY_REQUEST),
                "idempotency_key": f"replay:{inbox_id}:{uuid.uuid4().hex}",
                "source_system": SourceSystem.MANUAL,
                "source_message_id": f"replay:{inbox_id}:{uuid.uuid4().hex}",
                "workline_id": getattr(original, "workline_id", None),
                "device_id": getattr(original, "device_id", None),
                "command_id": getattr(original, "command_id", None),
                "session_id": getattr(original, "session_id", None),
                "trace_id": getattr(original, "trace_id", None),
                "event_id": replay_event_id,
                "causation_id": original_event_id or getattr(original, "causation_id", None),
                "payload_json": payload,
                "received_at": timezone.now_for_db(),
            },
        )
        if replay is None:
            raise RuntimeError(f"创建 replay inbox 失败: {inbox_id}")
        if auto_commit:
            await self._commit_mutation(db)
        return replay

    async def create_manual_operation(
        self,
        db: Any,
        *,
        session_id: int,
        operation: str,
        operator_id: str,
        reason: str,
        auto_commit: bool = True,
    ) -> Any:
        """创建人工操作 inbox。

        实际状态迁移和 timeline 由 runtime 消费该 inbox 后统一写入，避免 API 层提前写入
        与真实编排结果不一致的时间线。
        """

        session = await self.session_repo.get_by_id(db, session_id)
        if session is None:
            raise ValueError(f"会话不存在: {session_id}")
        if getattr(session, "status", None) not in _OPEN_SESSION_STATUSES:
            raise ValueError(f"当前会话状态不允许人工操作: session_id={session_id}")

        normalized_operation = operation.upper()
        kind = _MANUAL_OPERATION_KIND.get(normalized_operation)
        if kind is None:
            raise ValueError(f"不支持的人工操作: {operation}")

        trace = TraceContext.from_runtime(session=session)
        payload = {
            "message_type": "MANUAL_OPERATION",
            "operation": normalized_operation,
            "operator_id": operator_id,
            "reason": reason,
            "session_id": session_id,
        }
        inbox = await self.inbox_repo.create(
            db,
            {
                "kind": kind,
                "idempotency_key": f"manual:{session_id}:{normalized_operation}:{uuid.uuid4().hex}",
                "source_system": SourceSystem.MANUAL,
                "source_message_id": f"manual:{uuid.uuid4().hex}",
                "session_id": session_id,
                "workline_id": getattr(session, "workline_id", None),
                "trace_id": trace.trace_id,
                "payload_json": payload,
                "received_at": timezone.now_for_db(),
            },
        )
        if inbox is None:
            raise RuntimeError(f"创建人工操作 inbox 失败: session_id={session_id}")

        if auto_commit:
            await self._commit_mutation(db)
        return inbox


workline_operation_service = WorklineOperationService()


__all__ = ["WorklineOperationService", "workline_operation_service"]
