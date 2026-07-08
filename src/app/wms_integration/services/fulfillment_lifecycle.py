"""WMS fulfillment lifecycle service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.app.runtime.orchestration.services.idempotency_guard import (
    ClaimResult,
    IdempotencyGuard,
)
from src.app.runtime.orchestration.services.idempotency_guard import (
    idempotency_guard as default_idempotency_guard,
)
from src.app.wms_integration.state_machine import (
    FulfillmentEvent,
    FulfillmentState,
    WmsFulfillmentStateMachine,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class WmsFulfillmentLifecycleRecord:
    """WMS 履约生命周期快照。"""

    request_id: str
    fulfillment_kind: str
    state: FulfillmentState
    last_reason: str
    updated_at: datetime
    dispatch_allowed: bool = False
    runtime_inbox_required: bool = False
    history: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class WmsFulfillmentOpenResult:
    """带幂等 claim 结果的 WMS fulfillment opening 输出。"""

    record: WmsFulfillmentLifecycleRecord
    claim_result: ClaimResult


class WmsFulfillmentLifecycleService:
    """封装 WMS fulfillment 11 态机与 CB/callback 语义。"""

    def __init__(
        self,
        state_machine: WmsFulfillmentStateMachine | None = None,
        idempotency_guard: IdempotencyGuard = default_idempotency_guard,
    ) -> None:
        self.state_machine = state_machine or WmsFulfillmentStateMachine()
        self.idempotency_guard = idempotency_guard

    def open_request(
        self,
        *,
        request_id: str,
        fulfillment_kind: str,
        now: datetime,
        circuit_breaker_open: bool,
    ) -> WmsFulfillmentLifecycleRecord:
        """创建履约请求初始状态。"""

        if circuit_breaker_open:
            transition = self.state_machine.transition(
                current=FulfillmentState.REQUESTED,
                event=FulfillmentEvent.CIRCUIT_BREAKER_OPEN,
                now=now,
            )
            return WmsFulfillmentLifecycleRecord(
                request_id=request_id,
                fulfillment_kind=fulfillment_kind,
                state=transition.state,
                last_reason=transition.reason,
                updated_at=now,
                dispatch_allowed=False,
                runtime_inbox_required=transition.runtime_inbox_required,
                history=(transition.reason,),
            )
        return WmsFulfillmentLifecycleRecord(
            request_id=request_id,
            fulfillment_kind=fulfillment_kind,
            state=FulfillmentState.REQUESTED,
            last_reason="REQUESTED",
            updated_at=now,
            dispatch_allowed=True,
            history=("REQUESTED",),
        )

    async def open_request_idempotent(
        self,
        db: AsyncSession,
        *,
        request_id: str,
        fulfillment_kind: str,
        now: datetime,
        circuit_breaker_open: bool,
        provider_code: str,
        idempotency_key: str,
        request_hash: str,
        execution_correlation_id: str,
        now_ms: int,
        business_owner_key: str | None = None,
    ) -> WmsFulfillmentOpenResult:
        """先 claim fulfillment 幂等键，再创建履约生命周期初始快照。"""

        claim_result = await self.idempotency_guard.claim_or_match(
            db,
            provider_code=provider_code,
            operation_kind="fulfillment",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            execution_correlation_id=execution_correlation_id,
            now_ms=now_ms,
            business_owner_key=business_owner_key,
        )
        return WmsFulfillmentOpenResult(
            record=self.open_request(
                request_id=request_id,
                fulfillment_kind=fulfillment_kind,
                now=now,
                circuit_breaker_open=circuit_breaker_open,
            ),
            claim_result=claim_result,
        )

    def apply_event(
        self,
        record: WmsFulfillmentLifecycleRecord,
        event: FulfillmentEvent,
        *,
        now: datetime,
    ) -> WmsFulfillmentLifecycleRecord:
        """按状态机事件推进履约生命周期。"""

        transition = self.state_machine.transition(current=record.state, event=event, now=now)
        dispatch_allowed = transition.should_dispatch_effect and transition.state == FulfillmentState.SENT
        return WmsFulfillmentLifecycleRecord(
            request_id=record.request_id,
            fulfillment_kind=record.fulfillment_kind,
            state=transition.state,
            last_reason=transition.reason,
            updated_at=transition.occurred_at,
            dispatch_allowed=dispatch_allowed,
            runtime_inbox_required=transition.runtime_inbox_required,
            history=(*record.history, transition.reason),
        )


wms_fulfillment_lifecycle_service = WmsFulfillmentLifecycleService()


__all__ = [
    "WmsFulfillmentLifecycleRecord",
    "WmsFulfillmentLifecycleService",
    "WmsFulfillmentOpenResult",
    "wms_fulfillment_lifecycle_service",
]
