"""WMS fulfillment lifecycle service for Phase 3."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.app.wms_integration.state_machine import (
    FulfillmentEvent,
    FulfillmentState,
    WmsFulfillmentStateMachine,
)

if TYPE_CHECKING:
    from datetime import datetime


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


class WmsFulfillmentLifecycleService:
    """封装 WMS fulfillment 11 态机与 CB/callback 语义。"""

    def __init__(self, state_machine: WmsFulfillmentStateMachine | None = None) -> None:
        self.state_machine = state_machine or WmsFulfillmentStateMachine()

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
    "wms_fulfillment_lifecycle_service",
]
