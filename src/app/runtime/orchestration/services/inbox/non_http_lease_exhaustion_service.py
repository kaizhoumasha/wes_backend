"""非 HTTP 派发 lease 重试耗尽闭环。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.effect_state_contract import transition_dispatch_attempt
from src.app.runtime.orchestration.repositories.dispatch_attempt_repository import (
    WorklineDispatchAttemptRepository,
    workline_dispatch_attempt_repository,
)
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository, system_outbox_repository

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from src.app.sys.dispatch_concurrency import DispatchBucketKey

NON_HTTP_RETRY_EXHAUSTED_ERROR_CODE = "NON_HTTP_DISPATCH_RETRY_BUDGET_EXHAUSTED"


class NonHttpLeaseExhaustionService:
    """在同一 savepoint 终结耗尽预算的 outbox 与活动 attempt。"""

    def __init__(
        self,
        *,
        outbox_repository: SystemOutboxRepository = system_outbox_repository,
        dispatch_attempt_repository: WorklineDispatchAttemptRepository = workline_dispatch_attempt_repository,
    ) -> None:
        self.outbox_repository = outbox_repository
        self.dispatch_attempt_repository = dispatch_attempt_repository

    async def fence_exhausted_leases(
        self,
        db: Any,
        *,
        bucket: DispatchBucketKey,
        retry_budget: int,
        now: Any,
        operation_domains: tuple[str, ...] | None = None,
        exclude_operation_domains: tuple[str, ...] | None = None,
        repository: Any | None = None,
    ) -> int:
        selected_repository = repository or self.outbox_repository
        begin_nested = getattr(db, "begin_nested", None)
        if not callable(begin_nested):
            return await self._fence_in_transaction(
                db,
                repository=selected_repository,
                bucket=bucket,
                retry_budget=retry_budget,
                now=now,
                operation_domains=operation_domains,
                exclude_operation_domains=exclude_operation_domains,
            )
        begin_nested = cast("Callable[[], AbstractAsyncContextManager[Any]]", begin_nested)
        async with begin_nested():
            return await self._fence_in_transaction(
                db,
                repository=selected_repository,
                bucket=bucket,
                retry_budget=retry_budget,
                now=now,
                operation_domains=operation_domains,
                exclude_operation_domains=exclude_operation_domains,
            )

    async def _fence_in_transaction(
        self,
        db: Any,
        *,
        repository: Any,
        bucket: DispatchBucketKey,
        retry_budget: int,
        now: Any,
        operation_domains: tuple[str, ...] | None,
        exclude_operation_domains: tuple[str, ...] | None,
    ) -> int:
        outboxes = await repository.fence_exhausted_non_http_leases_in_bucket(
            db,
            bucket=bucket,
            retry_budget=retry_budget,
            now=now,
            operation_domains=operation_domains,
            exclude_operation_domains=exclude_operation_domains,
        )
        for outbox in outboxes:
            outbox_id = getattr(outbox, "id", None)
            lease_token = getattr(outbox, "lease_owner_token", None)
            if not isinstance(outbox_id, int) or not isinstance(lease_token, str) or not lease_token:
                raise RuntimeError("exhausted non-HTTP outbox is missing lease identity")
            attempt = await self.dispatch_attempt_repository.get_expired_dispatching_for_update(
                db,
                outbox_id=outbox_id,
                lease_token=lease_token,
                now=now,
            )
            if attempt is None:
                raise RuntimeError(f"active dispatch attempt is missing: outbox_id={outbox_id}")
            transition_dispatch_attempt(attempt, DispatchAttemptStatus.FAILED)
            attempt.finalized_at = now
            attempt.error_message = NON_HTTP_RETRY_EXHAUSTED_ERROR_CODE
            attempt.response_json = {
                "retry_budget_exhausted": True,
                "lease_expired": True,
            }
        if outboxes:
            await db.flush()
        return len(outboxes)


non_http_lease_exhaustion_service = NonHttpLeaseExhaustionService()

__all__ = ["NonHttpLeaseExhaustionService", "non_http_lease_exhaustion_service"]
