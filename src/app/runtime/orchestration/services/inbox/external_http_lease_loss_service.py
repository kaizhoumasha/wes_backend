"""过期 EXTERNAL_HTTP lease 的 attempt 与 EFFECT 证据闭环。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.effect_state_contract import transition_dispatch_attempt
from src.app.runtime.orchestration.models.dispatch_attempt import DispatchAttemptStatus
from src.app.runtime.orchestration.repositories.dispatch_attempt_repository import (
    WorklineDispatchAttemptRepository,
    workline_dispatch_attempt_repository,
)
from src.app.sys.external_http_transport import ExternalHttpTransportPhase, ExternalHttpTransportResult
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository, system_outbox_repository
from src.utils.timezone import timezone

LEASE_LOSS_ERROR_CODE = "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED"
LEASE_LOSS_ERROR_MESSAGE = "delivery evidence unavailable; automatic replay fenced"


class ExternalHttpLeaseLossService:
    """在一个 savepoint 内原子收口 outbox、attempt 与 EFFECT reducer。"""

    def __init__(
        self,
        *,
        outbox_repository: SystemOutboxRepository = system_outbox_repository,
        dispatch_attempt_repository: WorklineDispatchAttemptRepository = workline_dispatch_attempt_repository,
        effect_transport_bridge: Any | None = None,
    ) -> None:
        self.outbox_repository = outbox_repository
        self.dispatch_attempt_repository = dispatch_attempt_repository
        self.effect_transport_bridge = effect_transport_bridge

    def _resolve_effect_transport_bridge(self) -> Any:
        if self.effect_transport_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_transport_bridge

            self.effect_transport_bridge = effect_transport_bridge
        return self.effect_transport_bridge

    async def fence_expired_leases(
        self,
        db: Any,
        *,
        now: Any,
        operation_domains: tuple[str, ...] | None = None,
        exclude_operation_domains: tuple[str, ...] | None = None,
        outbox_repository: Any | None = None,
    ) -> int:
        """原子写入 lease-loss 的三账本证据；任一步失败都回滚本批闭环。"""

        repository = outbox_repository or self.outbox_repository
        begin_nested = getattr(db, "begin_nested", None)
        if not callable(begin_nested):
            return await self._fence_in_transaction(
                db,
                repository=repository,
                now=now,
                operation_domains=operation_domains,
                exclude_operation_domains=exclude_operation_domains,
            )
        async with begin_nested():
            return await self._fence_in_transaction(
                db,
                repository=repository,
                now=now,
                operation_domains=operation_domains,
                exclude_operation_domains=exclude_operation_domains,
            )

    async def _fence_in_transaction(
        self,
        db: Any,
        *,
        repository: Any,
        now: Any,
        operation_domains: tuple[str, ...] | None,
        exclude_operation_domains: tuple[str, ...] | None,
    ) -> int:
        fences = await repository.fence_expired_external_http_leases(
            db,
            now=now,
            operation_domains=operation_domains,
            exclude_operation_domains=exclude_operation_domains,
        )
        # 保持调度器测试 double 的旧式 int 协议；生产 Repository 返回冻结快照。
        if isinstance(fences, int):
            return fences

        result = ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code=LEASE_LOSS_ERROR_CODE,
            error_message=LEASE_LOSS_ERROR_MESSAGE,
        )
        occurred_at_ms = int(timezone.to_utc(now).timestamp() * 1000)
        for fence in fences:
            attempt = await self.dispatch_attempt_repository.get_expired_dispatching_for_update(
                db,
                outbox_id=fence.outbox_id,
                lease_token=fence.lease_owner_token,
                now=now,
            )
            attempt_no = fence.attempt_no_hint
            if attempt is not None:
                attempt_no = int(attempt.attempt_no)
                transition_dispatch_attempt(attempt, DispatchAttemptStatus.UNKNOWN)
                attempt.transport_outcome = result.outcome.value
                attempt.transport_phase = result.phase.value
                attempt.protocol_result = result.protocol_result.value
                attempt.safe_to_retry = result.safe_to_retry
                attempt.http_status_code = result.http_status_code
                attempt.finalized_at = now
                attempt.error_message = result.error_message
                attempt.response_json = {
                    "transport": result.evidence_json(),
                    "outbox_finalization": "unknown",
                    "lease_loss": True,
                }
                await db.flush()
            await self._resolve_effect_transport_bridge().record_result(
                db,
                dispatch_key=fence.dispatch_key,
                attempt_no=attempt_no,
                result=result,
                retry_exhausted=False,
                occurred_at_ms=occurred_at_ms,
            )
        return len(fences)


external_http_lease_loss_service = ExternalHttpLeaseLossService()

__all__ = ["ExternalHttpLeaseLossService", "external_http_lease_loss_service"]
