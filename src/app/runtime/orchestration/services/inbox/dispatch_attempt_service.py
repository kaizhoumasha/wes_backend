"""工作线派发尝试账本 Service。"""

from __future__ import annotations

from datetime import datetime, timedelta
from inspect import isawaitable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.effect_state_contract import transition_dispatch_attempt
from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.runtime.orchestration.repositories.dispatch_attempt_repository import (
    WorklineDispatchAttemptRepository,
    workline_dispatch_attempt_repository,
)
from src.app.sys.external_http_transport import ExternalHttpTransportOutcome, ExternalHttpTransportResult
from src.app.workline.trace_context import TraceContext
from src.core.base_service import BaseService
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value, optional_enum_str


class OutboxLeaseLost(RuntimeError):
    """attempt owner token 不再持有有效 SystemOutbox lease。"""

    code = "OUTBOX_LEASE_LOST"


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
    lease_owner_token: str,
    success: bool,
    response: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> WorklineDispatchAttempt:
    _assert_attempt_lease_current(attempt, lease_owner_token=lease_owner_token)
    transition_dispatch_attempt(
        attempt,
        DispatchAttemptStatus.SENT if success else DispatchAttemptStatus.FAILED,
    )
    attempt.finalized_at = timezone.now_for_db()
    attempt.response_json = response or {}
    attempt.error_message = None if success else error_message
    await _flush_if_supported(db)
    return attempt


async def _finalize_external_http_attempt(
    db: Any,
    *,
    attempt: WorklineDispatchAttempt,
    lease_owner_token: str,
    result: ExternalHttpTransportResult,
    outbox_finalization: str,
) -> WorklineDispatchAttempt:
    _assert_attempt_lease_current(attempt, lease_owner_token=lease_owner_token)
    status_by_outcome = {
        ExternalHttpTransportOutcome.NOT_SENT: DispatchAttemptStatus.FAILED,
        ExternalHttpTransportOutcome.ACCEPTED: DispatchAttemptStatus.SENT,
        ExternalHttpTransportOutcome.AMBIGUOUS: DispatchAttemptStatus.UNKNOWN,
    }
    transition_dispatch_attempt(attempt, status_by_outcome[result.outcome])
    attempt.transport_outcome = result.outcome.value
    attempt.transport_phase = result.phase.value
    attempt.protocol_result = result.protocol_result.value
    attempt.safe_to_retry = result.safe_to_retry
    attempt.http_status_code = result.http_status_code
    attempt.finalized_at = timezone.now_for_db()
    attempt.error_message = result.error_message
    attempt.response_json = {
        "transport": result.evidence_json(),
        "outbox_finalization": outbox_finalization,
    }
    await _flush_if_supported(db)
    return attempt


def _assert_attempt_lease_current(attempt: WorklineDispatchAttempt, *, lease_owner_token: str) -> None:
    expires_at = getattr(attempt, "lease_expires_at", None)
    if (
        enum_value(getattr(attempt, "status", None)) != DispatchAttemptStatus.DISPATCHING.value
        or getattr(attempt, "lease_token", None) != lease_owner_token
        or not isinstance(expires_at, datetime)
        or expires_at <= timezone.now_for_db()
    ):
        raise OutboxLeaseLost(
            f"OUTBOX_LEASE_LOST: outbox_id={getattr(attempt, 'outbox_id', None)}, "
            f"attempt_no={getattr(attempt, 'attempt_no', None)}"
        )


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

        lease_owner_token = getattr(outbox, "lease_owner_token", None)
        lease_expires_at = getattr(outbox, "lease_expires_at", None)
        if (
            enum_value(getattr(outbox, "status", None)) != "DISPATCHING"
            or not isinstance(lease_owner_token, str)
            or not lease_owner_token
            or not isinstance(lease_expires_at, datetime)
            or lease_expires_at <= timezone.now_for_db()
        ):
            raise OutboxLeaseLost(f"OUTBOX_LEASE_LOST: outbox_id={outbox_id} 没有有效 owner lease")

        existing_attempts = await self.repo.get_by_outbox_id(db, outbox_id)
        for existing in existing_attempts:
            if enum_value(getattr(existing, "status", None)) != DispatchAttemptStatus.DISPATCHING.value:
                continue
            if getattr(existing, "lease_token", None) == lease_owner_token:
                return existing
            transition_dispatch_attempt(existing, DispatchAttemptStatus.CANCELLED)
            existing.finalized_at = timezone.now_for_db()
            existing.error_message = "OUTBOX_LEASE_REPLACED"
            existing.response_json = {"lease_loss": True, "replacement_owner": lease_owner_token}

        attempt_no = await _next_attempt_no(db, repository=self.repo, outbox_id=outbox_id, outbox=outbox)
        trace = TraceContext.from_runtime(outbox=outbox)
        data = {
            "outbox_id": outbox_id,
            "dispatch_key": str(getattr(outbox, "dispatch_key", "")),
            "attempt_no": attempt_no,
            "lease_token": lease_owner_token,
            "lease_expires_at": lease_expires_at,
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
            lease_owner_token=lease_token,
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
        lease_owner_token: str,
        success: bool,
        response: dict[str, Any] | None = None,
        error_message: str | None = None,
        auto_commit: bool = True,
    ) -> WorklineDispatchAttempt:
        """结束当前事务中已持有的 attempt 实例。"""

        attempt = await _finalize_attempt(
            db,
            attempt=attempt,
            lease_owner_token=lease_owner_token,
            success=success,
            response=response,
            error_message=error_message,
        )
        if auto_commit:
            await self._commit_mutation(db)
        return attempt

    async def finalize_external_http_attempt_record(
        self,
        db: Any,
        *,
        attempt: WorklineDispatchAttempt,
        lease_owner_token: str,
        result: ExternalHttpTransportResult,
        outbox_finalization: str,
        auto_commit: bool = True,
    ) -> WorklineDispatchAttempt:
        """以 typed transport result 终结 EXTERNAL_HTTP attempt 并固化原始证据。"""

        attempt = await _finalize_external_http_attempt(
            db,
            attempt=attempt,
            lease_owner_token=lease_owner_token,
            result=result,
            outbox_finalization=outbox_finalization,
        )
        if auto_commit:
            await self._commit_mutation(db)
        return attempt

    async def finalize_external_http_attempt(
        self,
        db: Any,
        *,
        lease_token: str,
        result: ExternalHttpTransportResult,
        outbox_finalization: str,
        auto_commit: bool = True,
    ) -> WorklineDispatchAttempt:
        """按 lease token 在独立恢复事务中终结 EXTERNAL_HTTP attempt。"""

        attempt = await self.repo.get_by_lease_token(db, lease_token)
        if attempt is None:
            raise ValueError(f"派发尝试不存在: {lease_token}")
        attempt = await _finalize_external_http_attempt(
            db,
            attempt=attempt,
            lease_owner_token=lease_token,
            result=result,
            outbox_finalization=outbox_finalization,
        )
        if auto_commit:
            await self._commit_mutation(db)
        return attempt

    async def append_status_resubmit_result(
        self,
        db: Any,
        *,
        outbox: Any,
        result: ExternalHttpTransportResult,
        auto_commit: bool = True,
    ) -> WorklineDispatchAttempt:
        """为状态确认阶段同键重提追加 transport evidence，不改写 Outbox。"""

        outbox_id = getattr(outbox, "id", None)
        if not isinstance(outbox_id, int):
            raise TypeError("状态确认重提需要有效 outbox_id")
        now = timezone.now_for_db()
        lease_token = f"status-resubmit:{uuid4().hex}"
        attempt = WorklineDispatchAttempt(
            outbox_id=outbox_id,
            dispatch_key=str(getattr(outbox, "dispatch_key", "")),
            attempt_no=await _next_attempt_no(
                db,
                repository=self.repo,
                outbox_id=outbox_id,
                outbox=outbox,
            ),
            lease_token=lease_token,
            lease_expires_at=now + timedelta(minutes=5),
            status=DispatchAttemptStatus.DISPATCHING,
            target_type=optional_enum_str(getattr(outbox, "target_type", None)),
            target_code=getattr(outbox, "target_code", None),
            started_at=now,
            trace_json=TraceContext.from_runtime(outbox=outbox).project_outbox_trace(outbox=outbox),
        )
        db.add(attempt)
        await _flush_if_supported(db)
        await _finalize_external_http_attempt(
            db,
            attempt=attempt,
            lease_owner_token=lease_token,
            result=result,
            outbox_finalization=enum_value(getattr(outbox, "status", None)).lower(),
        )
        attempt.response_json = {
            **dict(attempt.response_json),
            "status_confirmation_resubmit": True,
        }
        await _flush_if_supported(db)
        if auto_commit:
            await self._commit_mutation(db)
        return attempt


workline_dispatch_attempt_service = WorklineDispatchAttemptService()


__all__ = ["OutboxLeaseLost", "WorklineDispatchAttemptService", "workline_dispatch_attempt_service"]
