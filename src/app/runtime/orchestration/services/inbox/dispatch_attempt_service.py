"""工作线派发尝试账本 Service。"""

from __future__ import annotations

import uuid
from inspect import isawaitable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from src.app.runtime.orchestration.effect_state_contract import transition_dispatch_attempt
from src.app.runtime.orchestration.models.dispatch_attempt import (
    DispatchAttemptStatus,
    WorklineDispatchAttempt,
)
from src.app.runtime.orchestration.repositories.dispatch_attempt_repository import (
    WorklineDispatchAttemptRepository,
    workline_dispatch_attempt_repository,
)
from src.app.workline.trace_context import TraceContext
from src.core.base_service import BaseService
from src.utils.timezone import timezone
from src.utils.value_normalization import optional_enum_str


async def _flush_if_supported(db: Any) -> None:
    flush = getattr(db, "flush", None)
    if not callable(flush):
        return

    flush_result = flush()
    if isawaitable(flush_result):
        await flush_result


async def _finalize_attempt(
    db: Any,
    *,
    attempt: WorklineDispatchAttempt,
    success: bool,
    response: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> WorklineDispatchAttempt:
    transition_dispatch_attempt(
        attempt,
        DispatchAttemptStatus.SENT if success else DispatchAttemptStatus.FAILED,
    )
    attempt.finalized_at = timezone.now_for_db()
    attempt.response_json = response or {}
    attempt.error_message = None if success else error_message
    await _flush_if_supported(db)
    return attempt


async def _next_attempt_no(
    db: AsyncSession,
    *,
    repository: WorklineDispatchAttemptRepository,
    outbox_id: int,
    outbox: Any,
) -> int:
    outbox_attempt_count = int(getattr(outbox, "attempt_count", 0) or 0)
    history_max_attempt_no = 0
    attempts = await repository.get_by_outbox_id(db, outbox_id)
    history_max_attempt_no = max(
        (int(getattr(attempt, "attempt_no", 0) or 0) for attempt in attempts),
        default=0,
    )

    return max(outbox_attempt_count, history_max_attempt_no) + 1


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

        attempt_no = await _next_attempt_no(db, repository=self.repo, outbox_id=outbox_id, outbox=outbox)
        lease_token = f"dispatch-attempt:{outbox_id}:{attempt_no}:{uuid.uuid4().hex}"
        trace = TraceContext.from_runtime(outbox=outbox)
        data = {
            "outbox_id": outbox_id,
            "dispatch_key": str(getattr(outbox, "dispatch_key", "")),
            "attempt_no": attempt_no,
            "lease_token": lease_token,
            "status": DispatchAttemptStatus.DISPATCHING.value,
            "target_type": optional_enum_str(getattr(outbox, "target_type", None)),
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
            await _flush_if_supported(db)
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

        attempt = await _finalize_attempt(
            db,
            attempt=attempt,
            success=success,
            response=response,
            error_message=error_message,
        )
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

        attempt = await _finalize_attempt(
            db,
            attempt=attempt,
            success=success,
            response=response,
            error_message=error_message,
        )
        if auto_commit:
            await self._commit_mutation(db)
        return attempt


workline_dispatch_attempt_service = WorklineDispatchAttemptService()


__all__ = ["WorklineDispatchAttemptService", "workline_dispatch_attempt_service"]
