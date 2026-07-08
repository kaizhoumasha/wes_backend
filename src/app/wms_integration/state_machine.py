"""WMS fulfillment state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from datetime import datetime


class FulfillmentState(str, Enum):
    """WMS 履约 11 态最小枚举。"""

    REQUESTED = "REQUESTED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    BLOCKED_BY_CB = "BLOCKED_BY_CB"
    RECONCILING = "RECONCILING"


class FulfillmentEvent(str, Enum):
    """WMS 履约状态机事件。"""

    DISPATCH_SENT = "DISPATCH_SENT"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    PROVIDER_RUNNING = "PROVIDER_RUNNING"
    CALLBACK_SUCCEEDED = "CALLBACK_SUCCEEDED"
    CALLBACK_FAILED = "CALLBACK_FAILED"
    CANCEL = "CANCEL"
    TIMEOUT = "TIMEOUT"
    REQUEST_DISPATCH_TIMEOUT = "REQUEST_DISPATCH_TIMEOUT"
    SENT_ACK_TIMEOUT = "SENT_ACK_TIMEOUT"
    ACCEPTED_RUNNING_TIMEOUT = "ACCEPTED_RUNNING_TIMEOUT"
    RUNNING_RESULT_TIMEOUT = "RUNNING_RESULT_TIMEOUT"
    CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"


@dataclass(frozen=True, slots=True)
class FulfillmentTransitionResult:
    """履约状态转移结果。"""

    state: FulfillmentState
    occurred_at: datetime
    reason: str
    counts_as_sent: bool = False
    should_dispatch_effect: bool = False
    runtime_inbox_required: bool = False


class WmsFulfillmentStateMachine:
    """WMS fulfillment 目标态最小状态机。"""

    _TERMINAL_STATES: ClassVar[frozenset[FulfillmentState]] = frozenset(
        {
            FulfillmentState.SUCCEEDED,
            FulfillmentState.REJECTED,
            FulfillmentState.FAILED,
            FulfillmentState.TIMEOUT,
            FulfillmentState.CANCELLED,
        }
    )
    _CALLBACK_EVENTS: ClassVar[frozenset[FulfillmentEvent]] = frozenset(
        {
            FulfillmentEvent.CALLBACK_SUCCEEDED,
            FulfillmentEvent.CALLBACK_FAILED,
        }
    )
    _PROVIDER_TRANSITIONS: ClassVar[dict[tuple[FulfillmentState, FulfillmentEvent], tuple[FulfillmentState, str]]] = {
        (FulfillmentState.SENT, FulfillmentEvent.PROVIDER_ACCEPTED): (
            FulfillmentState.ACCEPTED,
            "PROVIDER_ACCEPTED",
        ),
        (FulfillmentState.SENT, FulfillmentEvent.PROVIDER_REJECTED): (
            FulfillmentState.REJECTED,
            "PROVIDER_REJECTED",
        ),
        (FulfillmentState.ACCEPTED, FulfillmentEvent.PROVIDER_RUNNING): (
            FulfillmentState.RUNNING,
            "PROVIDER_RUNNING",
        ),
    }
    _TIMEOUT_TRANSITIONS: ClassVar[dict[tuple[FulfillmentState, FulfillmentEvent], str]] = {
        (FulfillmentState.REQUESTED, FulfillmentEvent.REQUEST_DISPATCH_TIMEOUT): "REQUEST_DISPATCH_TIMEOUT",
        (FulfillmentState.SENT, FulfillmentEvent.SENT_ACK_TIMEOUT): "SENT_ACK_TIMEOUT",
        (FulfillmentState.ACCEPTED, FulfillmentEvent.ACCEPTED_RUNNING_TIMEOUT): "ACCEPTED_RUNNING_TIMEOUT",
        (FulfillmentState.RUNNING, FulfillmentEvent.RUNNING_RESULT_TIMEOUT): "RUNNING_RESULT_TIMEOUT",
    }

    def transition(  # noqa: PLR0911 - explicit transition exits keep state-table semantics readable.
        self,
        *,
        current: FulfillmentState,
        event: FulfillmentEvent,
        now: datetime,
    ) -> FulfillmentTransitionResult:
        if current in self._TERMINAL_STATES:
            return FulfillmentTransitionResult(
                state=current,
                occurred_at=now,
                reason="TERMINAL_STATE_IGNORED",
                runtime_inbox_required=event in self._CALLBACK_EVENTS,
            )

        if current == FulfillmentState.RECONCILING:
            return FulfillmentTransitionResult(
                state=FulfillmentState.RECONCILING,
                occurred_at=now,
                reason="RECONCILING_STATE_IGNORED",
                runtime_inbox_required=event in self._CALLBACK_EVENTS,
            )

        if event == FulfillmentEvent.CIRCUIT_BREAKER_OPEN and current == FulfillmentState.REQUESTED:
            return FulfillmentTransitionResult(
                state=FulfillmentState.BLOCKED_BY_CB,
                occurred_at=now,
                reason="CIRCUIT_BREAKER_OPEN",
            )
        if event == FulfillmentEvent.CIRCUIT_BREAKER_OPEN:
            return FulfillmentTransitionResult(
                state=current,
                occurred_at=now,
                reason="CIRCUIT_BREAKER_OPEN_OUTBOUND_ONLY",
            )

        if current == FulfillmentState.BLOCKED_BY_CB and event in {
            FulfillmentEvent.CALLBACK_SUCCEEDED,
            FulfillmentEvent.CALLBACK_FAILED,
        }:
            return FulfillmentTransitionResult(
                state=FulfillmentState.RECONCILING,
                occurred_at=now,
                reason="LATE_CALLBACK_WHILE_CB_BLOCKED",
                runtime_inbox_required=True,
            )
        if current == FulfillmentState.BLOCKED_BY_CB and event == FulfillmentEvent.DISPATCH_SENT:
            return FulfillmentTransitionResult(
                state=FulfillmentState.BLOCKED_BY_CB,
                occurred_at=now,
                reason="DISPATCH_BLOCKED_BY_CB",
            )

        if event == FulfillmentEvent.DISPATCH_SENT:
            if current != FulfillmentState.REQUESTED:
                return self._unsupported(now, runtime_inbox_required=False)
            return FulfillmentTransitionResult(
                state=FulfillmentState.SENT,
                occurred_at=now,
                reason="DISPATCH_SENT",
                counts_as_sent=True,
                should_dispatch_effect=True,
            )

        if event == FulfillmentEvent.CANCEL:
            return FulfillmentTransitionResult(FulfillmentState.CANCELLED, now, "CANCEL")

        if event == FulfillmentEvent.TIMEOUT:
            return FulfillmentTransitionResult(FulfillmentState.TIMEOUT, now, "TIMEOUT")

        provider_transition = self._PROVIDER_TRANSITIONS.get((current, event))
        if provider_transition is not None:
            state, reason = provider_transition
            return FulfillmentTransitionResult(state, now, reason)

        timeout_reason = self._TIMEOUT_TRANSITIONS.get((current, event))
        if timeout_reason is not None:
            return FulfillmentTransitionResult(
                FulfillmentState.TIMEOUT,
                now,
                timeout_reason,
            )

        if event == FulfillmentEvent.CALLBACK_SUCCEEDED and current in {
            FulfillmentState.SENT,
            FulfillmentState.ACCEPTED,
            FulfillmentState.RUNNING,
        }:
            return FulfillmentTransitionResult(
                FulfillmentState.SUCCEEDED,
                now,
                "CALLBACK_SUCCEEDED",
                runtime_inbox_required=True,
            )
        if event == FulfillmentEvent.CALLBACK_FAILED and current in {
            FulfillmentState.SENT,
            FulfillmentState.ACCEPTED,
            FulfillmentState.RUNNING,
        }:
            return FulfillmentTransitionResult(
                FulfillmentState.FAILED,
                now,
                "CALLBACK_FAILED",
                runtime_inbox_required=True,
            )
        return self._unsupported(now, runtime_inbox_required=event in self._CALLBACK_EVENTS)

    @staticmethod
    def _unsupported(now: datetime, *, runtime_inbox_required: bool) -> FulfillmentTransitionResult:
        return FulfillmentTransitionResult(
            FulfillmentState.RECONCILING,
            now,
            "UNSUPPORTED_TRANSITION",
            runtime_inbox_required=runtime_inbox_required,
        )


__all__ = [
    "FulfillmentEvent",
    "FulfillmentState",
    "FulfillmentTransitionResult",
    "WmsFulfillmentStateMachine",
]
