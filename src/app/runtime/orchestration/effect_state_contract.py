"""EFFECT 双账本状态与 reducer event 合同。

本模块只冻结枚举、允许转移和事件 Schema；transport/callback/reconciliation
证据的单调归并由后续 reducer 生命周期实现。
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.app.runtime.orchestration.models.dispatch_attempt import DispatchAttemptStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.sys.models.outbox import SystemOutboxStatus


class EffectReducerEventType(str, Enum):
    """Reducer 可接受的封闭 evidence event 集合。"""

    INTENT_PROPOSED = "INTENT_PROPOSED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    TRANSPORT_NOT_SENT = "TRANSPORT_NOT_SENT"
    TRANSPORT_ACCEPTED = "TRANSPORT_ACCEPTED"
    TRANSPORT_AMBIGUOUS = "TRANSPORT_AMBIGUOUS"
    CALLBACK_ACCEPTED = "CALLBACK_ACCEPTED"
    CALLBACK_COMPLETED = "CALLBACK_COMPLETED"
    CALLBACK_REJECTED = "CALLBACK_REJECTED"
    RECONCILIATION_OPENED = "RECONCILIATION_OPENED"
    RECONCILIATION_RESOLVED = "RECONCILIATION_RESOLVED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


EFFECT_REDUCER_EVENT_TYPES = frozenset(EffectReducerEventType)

RUNTIME_INTENT_TRANSITIONS = MappingProxyType(
    {
        None: frozenset({RuntimeIntentStatus.PROPOSED}),
        RuntimeIntentStatus.PROPOSED: frozenset(
            {
                RuntimeIntentStatus.ACCEPTED,
                RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.TECHNICAL_FAILED,
                RuntimeIntentStatus.UNKNOWN,
            }
        ),
        RuntimeIntentStatus.ACCEPTED: frozenset(
            {
                RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.UNKNOWN,
                RuntimeIntentStatus.RECONCILING,
            }
        ),
        RuntimeIntentStatus.UNKNOWN: frozenset(
            {
                RuntimeIntentStatus.ACCEPTED,
                RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.RECONCILING,
            }
        ),
        RuntimeIntentStatus.RECONCILING: frozenset({RuntimeIntentStatus.COMPLETED, RuntimeIntentStatus.REJECTED}),
        RuntimeIntentStatus.COMPLETED: frozenset(),
        RuntimeIntentStatus.REJECTED: frozenset(),
        RuntimeIntentStatus.TECHNICAL_FAILED: frozenset(),
    }
)

SYSTEM_OUTBOX_TRANSITIONS = MappingProxyType(
    {
        None: frozenset({SystemOutboxStatus.NEW}),
        SystemOutboxStatus.NEW: frozenset({SystemOutboxStatus.DISPATCHING, SystemOutboxStatus.CANCELLED}),
        SystemOutboxStatus.RETRY_WAIT: frozenset({SystemOutboxStatus.DISPATCHING, SystemOutboxStatus.CANCELLED}),
        SystemOutboxStatus.DISPATCHING: frozenset(
            {
                SystemOutboxStatus.RETRY_WAIT,
                SystemOutboxStatus.SENT,
                SystemOutboxStatus.FAILED,
                SystemOutboxStatus.UNKNOWN,
                SystemOutboxStatus.CANCELLED,
            }
        ),
        SystemOutboxStatus.SENT: frozenset(),
        SystemOutboxStatus.FAILED: frozenset(),
        SystemOutboxStatus.UNKNOWN: frozenset(),
        SystemOutboxStatus.CANCELLED: frozenset(),
    }
)

DISPATCH_ATTEMPT_TRANSITIONS = MappingProxyType(
    {
        None: frozenset({DispatchAttemptStatus.DISPATCHING}),
        DispatchAttemptStatus.DISPATCHING: frozenset(
            {
                DispatchAttemptStatus.SENT,
                DispatchAttemptStatus.FAILED,
                DispatchAttemptStatus.UNKNOWN,
                DispatchAttemptStatus.CANCELLED,
            }
        ),
        DispatchAttemptStatus.SENT: frozenset(),
        DispatchAttemptStatus.FAILED: frozenset(),
        DispatchAttemptStatus.UNKNOWN: frozenset(),
        DispatchAttemptStatus.CANCELLED: frozenset(),
    }
)

_ATTEMPT_EVENTS = frozenset(
    {
        EffectReducerEventType.ATTEMPT_STARTED,
        EffectReducerEventType.TRANSPORT_NOT_SENT,
        EffectReducerEventType.TRANSPORT_ACCEPTED,
        EffectReducerEventType.TRANSPORT_AMBIGUOUS,
    }
)
_RECONCILIATION_TERMINALS = frozenset({RuntimeIntentStatus.COMPLETED, RuntimeIntentStatus.REJECTED})


class EffectReducerEvent(BaseModel):
    """Reducer 输入；冻结事实，不在 Schema 内执行状态归并。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: EffectReducerEventType
    dispatch_key: str = Field(min_length=1, max_length=240)
    occurred_at_ms: int = Field(ge=0)
    source_event_id: str | None = Field(default=None, max_length=240)
    attempt_no: int | None = Field(default=None, ge=1)
    retry_exhausted: bool = False
    resolution: RuntimeIntentStatus | None = None
    reason_code: str | None = Field(default=None, max_length=120)
    evidence_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event_shape(self) -> EffectReducerEvent:
        if self.event_type in _ATTEMPT_EVENTS and self.attempt_no is None:
            raise ValueError(f"{self.event_type.value} requires attempt_no")
        if self.event_type not in _ATTEMPT_EVENTS and self.attempt_no is not None:
            raise ValueError(f"{self.event_type.value} does not accept attempt_no")
        if self.event_type is EffectReducerEventType.RECONCILIATION_RESOLVED:
            if self.resolution not in _RECONCILIATION_TERMINALS:
                raise ValueError("RECONCILIATION_RESOLVED requires COMPLETED or REJECTED resolution")
        elif self.resolution is not None:
            raise ValueError(f"{self.event_type.value} does not accept resolution")
        if self.retry_exhausted and self.event_type is not EffectReducerEventType.TRANSPORT_NOT_SENT:
            raise ValueError("retry_exhausted is only valid for TRANSPORT_NOT_SENT")
        return self


__all__ = [
    "DISPATCH_ATTEMPT_TRANSITIONS",
    "EFFECT_REDUCER_EVENT_TYPES",
    "RUNTIME_INTENT_TRANSITIONS",
    "SYSTEM_OUTBOX_TRANSITIONS",
    "EffectReducerEvent",
    "EffectReducerEventType",
]
