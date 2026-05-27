"""WMS circuit breaker 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from src.app.wms_integration.models import WmsCircuitBreakerState, WmsCircuitBreakerStatus
from src.app.wms_integration.repositories import WmsCircuitBreakerRepository, wms_circuit_breaker_repository
from src.core.base_service import BaseService
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class WmsCircuitBreakerDecision:
    """WMS 调用前的 breaker 判定结果。

    HALF_OPEN 放行时会携带 probe_generation；调用方记录成功/失败时必须原样传回，
    用于避免旧在途请求污染当前探测窗口。CLOSED 正常调用不要求该字段。
    """

    allowed: bool
    state: WmsCircuitBreakerStatus
    target_code: str
    operation_name: str
    reason: str
    probe_generation: int | None = None
    retry_after_seconds: int | None = None


class WmsCircuitBreakerService(BaseService[WmsCircuitBreakerState, WmsCircuitBreakerRepository]):
    """DB-backed WMS 熔断器状态机。

    调用契约：
    - `before_call()` 必须在短事务中执行并提交，然后再发起外部 HTTP 调用；
    - `record_success()` / `record_failure()` 必须在外部调用结束后的独立短事务中执行；
    - HALF_OPEN 放行返回的 `probe_generation` 必须随调用结果传回，结果只会影响匹配的当前探针。

    该契约避免在外部网络调用期间持有 breaker 行锁，也确保 HALF_OPEN 探针状态能被其他实例看到。
    """

    def __init__(
        self,
        repository: WmsCircuitBreakerRepository | None = None,
        *,
        failure_threshold: int = 3,
        retry_after_seconds: int = 60,
        half_open_success_threshold: int = 1,
        half_open_max_attempts: int | None = None,
        half_open_probe_timeout_seconds: int | None = None,
    ) -> None:
        super().__init__(repository or wms_circuit_breaker_repository)
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if retry_after_seconds < 1:
            raise ValueError("retry_after_seconds must be >= 1")
        if half_open_success_threshold < 1:
            raise ValueError("half_open_success_threshold must be >= 1")
        if half_open_probe_timeout_seconds is not None and half_open_probe_timeout_seconds < 1:
            raise ValueError("half_open_probe_timeout_seconds must be >= 1")
        self.failure_threshold = failure_threshold
        self.retry_after_seconds = retry_after_seconds
        self.half_open_success_threshold = half_open_success_threshold
        self.half_open_max_attempts = half_open_max_attempts or half_open_success_threshold
        self.half_open_probe_timeout_seconds = half_open_probe_timeout_seconds or retry_after_seconds

    async def before_call(
        self,
        db: AsyncSession,
        *,
        target_code: str,
        operation_name: str,
        now: datetime | None = None,
    ) -> WmsCircuitBreakerDecision:
        """调用 WMS 前判定是否允许放行。

        该方法会锁定并更新 breaker 行。调用方应在提交当前短事务后再执行外部 HTTP。
        """

        current_time = now or timezone.now_for_db()
        state = await self.repo.get_or_create_for_update(db, target_code=target_code, operation_name=operation_name)

        if state.state == WmsCircuitBreakerStatus.CLOSED:
            await db.flush()
            return self._decision(state, allowed=True, reason="CLOSED_ALLOW")

        if state.state == WmsCircuitBreakerStatus.OPEN:
            if state.opened_until is not None and state.opened_until > current_time:
                await db.flush()
                return self._decision(
                    state,
                    allowed=False,
                    reason="OPEN_FAST_FAIL",
                    retry_after_seconds=max(0, int((state.opened_until - current_time).total_seconds())),
                )
            self._move_to_half_open(state, current_time)
            self._start_half_open_probe(state, current_time)
            await db.flush()
            return self._decision(state, allowed=True, reason="OPEN_RETRY_AFTER_ELAPSED", include_probe=True)

        if state.half_open_attempt_count >= self.half_open_max_attempts:
            if self._is_half_open_probe_expired(state, current_time):
                self._refresh_half_open_probe_window(state, current_time)
                await db.flush()
                return self._decision(state, allowed=True, reason="HALF_OPEN_PROBE_EXPIRED_RETRY", include_probe=True)
            await db.flush()
            return self._decision(state, allowed=False, reason="HALF_OPEN_TRIAL_IN_PROGRESS")

        self._start_half_open_probe(state, current_time)
        await db.flush()
        return self._decision(state, allowed=True, reason="HALF_OPEN_ALLOW", include_probe=True)

    async def record_success(
        self,
        db: AsyncSession,
        *,
        target_code: str,
        operation_name: str,
        evidence_key: str | None = None,
        probe_generation: int | None = None,
        now: datetime | None = None,
    ) -> WmsCircuitBreakerState:
        """记录一次 WMS 调用成功，并按状态机推进。

        HALF_OPEN 状态只接受与当前 `half_open_probe_generation` 匹配且未过期的结果。
        """

        current_time = now or timezone.now_for_db()
        state = await self.repo.get_or_create_for_update(db, target_code=target_code, operation_name=operation_name)

        if state.state == WmsCircuitBreakerStatus.HALF_OPEN:
            if not self._matches_half_open_probe(state, probe_generation, current_time):
                return state
            state.last_evidence_key = evidence_key
            state.half_open_success_count += 1
            if state.half_open_success_count >= self.half_open_success_threshold:
                self._move_to_closed(state, current_time)
        elif state.state == WmsCircuitBreakerStatus.CLOSED:
            state.last_evidence_key = evidence_key
            state.failure_count = 0
        else:
            state.last_evidence_key = evidence_key

        await db.flush()
        return state

    async def record_failure(
        self,
        db: AsyncSession,
        *,
        target_code: str,
        operation_name: str,
        evidence_key: str | None = None,
        probe_generation: int | None = None,
        now: datetime | None = None,
    ) -> WmsCircuitBreakerState:
        """记录一次 WMS 调用失败，并按状态机推进。

        HALF_OPEN 状态只接受与当前 `half_open_probe_generation` 匹配且未过期的结果。
        """

        current_time = now or timezone.now_for_db()
        state = await self.repo.get_or_create_for_update(db, target_code=target_code, operation_name=operation_name)

        if state.state == WmsCircuitBreakerStatus.HALF_OPEN:
            if not self._matches_half_open_probe(state, probe_generation, current_time):
                return state
            state.last_failure_at = current_time
            state.last_evidence_key = evidence_key
            self._move_to_open(state, current_time)
        elif state.state == WmsCircuitBreakerStatus.OPEN:
            state.last_failure_at = current_time
            state.last_evidence_key = evidence_key
            self._move_to_open(state, current_time)
        else:
            state.last_failure_at = current_time
            state.last_evidence_key = evidence_key
            state.failure_count += 1
            if state.failure_count >= self.failure_threshold:
                self._move_to_open(state, current_time)

        await db.flush()
        return state

    def _move_to_open(self, state: WmsCircuitBreakerState, now: datetime) -> None:
        state.state = WmsCircuitBreakerStatus.OPEN
        state.opened_until = now + timedelta(seconds=self.retry_after_seconds)
        state.half_open_attempt_count = 0
        state.half_open_success_count = 0
        state.half_open_probe_expires_at = None
        state.last_transition_at = now

    @staticmethod
    def _move_to_half_open(state: WmsCircuitBreakerState, now: datetime) -> None:
        state.state = WmsCircuitBreakerStatus.HALF_OPEN
        state.opened_until = None
        state.half_open_attempt_count = 0
        state.half_open_success_count = 0
        state.half_open_probe_generation += 1
        state.half_open_probe_expires_at = None
        state.last_transition_at = now

    @staticmethod
    def _move_to_closed(state: WmsCircuitBreakerState, now: datetime) -> None:
        state.state = WmsCircuitBreakerStatus.CLOSED
        state.failure_count = 0
        state.opened_until = None
        state.half_open_attempt_count = 0
        state.half_open_success_count = 0
        state.half_open_probe_expires_at = None
        state.last_transition_at = now

    def _start_half_open_probe(self, state: WmsCircuitBreakerState, now: datetime) -> None:
        state.half_open_attempt_count += 1
        state.half_open_probe_expires_at = now + timedelta(seconds=self.half_open_probe_timeout_seconds)

    def _refresh_half_open_probe_window(self, state: WmsCircuitBreakerState, now: datetime) -> None:
        state.half_open_attempt_count = 0
        state.half_open_success_count = 0
        state.half_open_probe_generation += 1
        self._start_half_open_probe(state, now)

    @staticmethod
    def _decision(
        state: WmsCircuitBreakerState,
        *,
        allowed: bool,
        reason: str,
        include_probe: bool = False,
        retry_after_seconds: int | None = None,
    ) -> WmsCircuitBreakerDecision:
        return WmsCircuitBreakerDecision(
            allowed=allowed,
            state=state.state,
            target_code=state.target_code,
            operation_name=state.operation_name,
            reason=reason,
            probe_generation=state.half_open_probe_generation if include_probe else None,
            retry_after_seconds=retry_after_seconds,
        )

    @staticmethod
    def _is_half_open_probe_expired(state: WmsCircuitBreakerState, now: datetime) -> bool:
        return state.half_open_probe_expires_at is None or state.half_open_probe_expires_at <= now

    @staticmethod
    def _matches_half_open_probe(
        state: WmsCircuitBreakerState,
        probe_generation: int | None,
        now: datetime,
    ) -> bool:
        return (
            probe_generation == state.half_open_probe_generation
            and state.half_open_probe_expires_at is not None
            and state.half_open_probe_expires_at > now
        )


wms_circuit_breaker_service = WmsCircuitBreakerService()


__all__ = [
    "WmsCircuitBreakerDecision",
    "WmsCircuitBreakerService",
    "wms_circuit_breaker_service",
]
