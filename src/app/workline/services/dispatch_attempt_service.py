"""工作线派发尝试账本 Service。"""

from __future__ import annotations

import uuid
from inspect import isawaitable
from typing import Any

from src.app.workline.models.dispatch_attempt import (
    DispatchAttemptStatus,
    WorklineDispatchAttempt,
)
from src.app.workline.repositories.dispatch_attempt_repository import (
    WorklineDispatchAttemptRepository,
    workline_dispatch_attempt_repository,
)
from src.core.base_service import BaseService
from src.utils.timezone import timezone
from src.workline_runtime.trace_context import TraceContext


def _enum_value(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else None


class WorklineDispatchAttemptService(BaseService[WorklineDispatchAttempt, WorklineDispatchAttemptRepository]):
    """维护 outbox 派发 attempt 的 lease 与 finalize 语义。"""

    def __init__(self, repository: WorklineDispatchAttemptRepository | None = None) -> None:
        super().__init__(repository or workline_dispatch_attempt_repository, enable_cache=False)

    async def create_attempt(
        self,
        db: Any,
        *,
        outbox: Any,
        auto_commit: bool = True,
    ) -> WorklineDispatchAttempt:
        """为一次 outbox 派发创建 lease。"""

        outbox_id = getattr(outbox, "id", None)
        if not isinstance(outbox_id, int):
            raise TypeError("创建派发尝试需要有效 outbox_id")

        attempt_no = int(getattr(outbox, "attempt_count", 0) or 0) + 1
        lease_token = f"dispatch-attempt:{outbox_id}:{attempt_no}:{uuid.uuid4().hex}"
        trace = TraceContext.from_runtime(outbox=outbox)
        data = {
            "outbox_id": outbox_id,
            "dispatch_key": str(getattr(outbox, "dispatch_key", "")),
            "attempt_no": attempt_no,
            "lease_token": lease_token,
            "status": DispatchAttemptStatus.DISPATCHING.value,
            "target_type": _enum_value(getattr(outbox, "target_type", None)),
            "target_code": getattr(outbox, "target_code", None),
            "started_at": timezone.now_for_db(),
            "trace_json": trace.project_outbox_trace(outbox=outbox),
        }
        add = getattr(db, "add", None)
        if callable(add):
            created = WorklineDispatchAttempt(**data)
            add_result = add(created)
            if isawaitable(add_result):
                await add_result
            flush = getattr(db, "flush", None)
            if callable(flush):
                flush_result = flush()
                if isawaitable(flush_result):
                    await flush_result
        else:
            created = await self.repo.create(db, data)
            if created is None:
                raise RuntimeError(f"创建派发尝试失败: outbox_id={outbox_id}")
        if auto_commit:
            await self._commit_mutation(db)
        return created

    async def finalize_attempt(
        self,
        db: Any,
        *,
        lease_token: str,
        success: bool,
        response: dict[str, Any] | None = None,
        error_message: str | None = None,
        auto_commit: bool = True,
    ) -> WorklineDispatchAttempt:
        """按 lease token 结束派发尝试。"""

        attempt = await self.repo.get_by_lease_token(db, lease_token)
        if attempt is None:
            raise ValueError(f"派发尝试不存在: {lease_token}")

        attempt.status = DispatchAttemptStatus.SENT.value if success else DispatchAttemptStatus.FAILED.value
        attempt.finalized_at = timezone.now_for_db()
        attempt.response_json = response or {}
        attempt.error_message = None if success else error_message
        flush = getattr(db, "flush", None)
        if callable(flush):
            flush_result = flush()
            if isawaitable(flush_result):
                await flush_result
        if auto_commit:
            await self._commit_mutation(db)
        return attempt

    async def finalize_attempt_record(
        self,
        db: Any,
        *,
        attempt: WorklineDispatchAttempt,
        success: bool,
        response: dict[str, Any] | None = None,
        error_message: str | None = None,
        auto_commit: bool = True,
    ) -> WorklineDispatchAttempt:
        """结束当前事务中已持有的 attempt 实例。"""

        attempt.status = DispatchAttemptStatus.SENT.value if success else DispatchAttemptStatus.FAILED.value
        attempt.finalized_at = timezone.now_for_db()
        attempt.response_json = response or {}
        attempt.error_message = None if success else error_message
        flush = getattr(db, "flush", None)
        if callable(flush):
            flush_result = flush()
            if isawaitable(flush_result):
                await flush_result
        if auto_commit:
            await self._commit_mutation(db)
        return attempt


workline_dispatch_attempt_service = WorklineDispatchAttemptService()


__all__ = ["WorklineDispatchAttemptService", "workline_dispatch_attempt_service"]
