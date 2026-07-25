"""EFFECT 双账本状态与 reducer event 合同。

本模块只冻结枚举、允许转移和事件 Schema；transport/callback/reconciliation
证据的单调归并由后续 reducer 生命周期实现。
"""

from __future__ import annotations

import json
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.app.effect_ledger_status import DispatchAttemptStatus, SystemOutboxStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus


class EffectReducerEventType(str, Enum):
    """Reducer 可接受的封闭 evidence event 集合。"""

    INTENT_PROPOSED = "INTENT_PROPOSED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    TRANSPORT_NOT_SENT = "TRANSPORT_NOT_SENT"
    TRANSPORT_ACCEPTED = "TRANSPORT_ACCEPTED"
    TRANSPORT_REJECTED = "TRANSPORT_REJECTED"
    TRANSPORT_AMBIGUOUS = "TRANSPORT_AMBIGUOUS"
    LOCAL_REDECISION_REQUIRED = "LOCAL_REDECISION_REQUIRED"
    DISPATCH_CANCELLED = "DISPATCH_CANCELLED"
    CALLBACK_ACCEPTED = "CALLBACK_ACCEPTED"
    CALLBACK_COMPLETED = "CALLBACK_COMPLETED"
    CALLBACK_REJECTED = "CALLBACK_REJECTED"
    STATUS_ACCEPTED = "STATUS_ACCEPTED"
    STATUS_PROCESSING = "STATUS_PROCESSING"
    STATUS_COMPLETED = "STATUS_COMPLETED"
    STATUS_REJECTED = "STATUS_REJECTED"
    STATUS_NOT_FOUND = "STATUS_NOT_FOUND"
    STATUS_QUERY_FAILED = "STATUS_QUERY_FAILED"
    STATUS_STALE = "STATUS_STALE"
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
                RuntimeIntentStatus.RECONCILING,
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
        EffectReducerEventType.TRANSPORT_REJECTED,
        EffectReducerEventType.TRANSPORT_AMBIGUOUS,
    }
)
_RECONCILIATION_TERMINALS = frozenset({RuntimeIntentStatus.COMPLETED, RuntimeIntentStatus.REJECTED})

_StatusT = TypeVar("_StatusT", bound=Enum)


class InvalidEffectTransition(ValueError):
    """状态写入不属于已冻结 transition matrix。"""


def _transition(
    subject: Any,
    *,
    attribute: str,
    target: _StatusT,
    status_type: type[_StatusT],
    transitions: Any,
    ledger_name: str,
) -> None:
    current_value = getattr(subject, attribute, None)
    try:
        current = status_type(current_value) if current_value is not None else None
        normalized_target = status_type(target)
    except (TypeError, ValueError) as exc:
        raise InvalidEffectTransition(f"{ledger_name} 包含未知状态: {current_value!r} -> {target!r}") from exc
    if normalized_target not in transitions.get(current, frozenset()):
        current_label = current.value if current is not None else "None"
        raise InvalidEffectTransition(f"{ledger_name} 非法状态转移: {current_label} -> {normalized_target.value}")
    setattr(subject, attribute, normalized_target)


def transition_runtime_intent(subject: Any, target: RuntimeIntentStatus) -> None:
    """按冻结矩阵写入 RuntimeIntentLog.effect_status。"""

    _transition(
        subject,
        attribute="effect_status",
        target=target,
        status_type=RuntimeIntentStatus,
        transitions=RUNTIME_INTENT_TRANSITIONS,
        ledger_name="RuntimeIntentLog",
    )


def transition_system_outbox(subject: Any, target: SystemOutboxStatus) -> None:
    """按冻结矩阵写入 SystemOutbox.status。"""

    _transition(
        subject,
        attribute="status",
        target=target,
        status_type=SystemOutboxStatus,
        transitions=SYSTEM_OUTBOX_TRANSITIONS,
        ledger_name="SystemOutbox",
    )


def transition_dispatch_attempt(subject: Any, target: DispatchAttemptStatus) -> None:
    """按冻结矩阵写入 DispatchAttempt.status。"""

    _transition(
        subject,
        attribute="status",
        target=target,
        status_type=DispatchAttemptStatus,
        transitions=DISPATCH_ATTEMPT_TRANSITIONS,
        ledger_name="DispatchAttempt",
    )


def generated_effect_source_event_id(namespace: str, *identity_parts: object) -> str:
    """为内部生成的 reducer evidence 构造有界、稳定且无拼接歧义的身份。"""

    normalized_namespace = namespace.strip()
    if not normalized_namespace or len(normalized_namespace) > 120:
        raise ValueError("effect source event namespace must contain 1..120 characters")
    material = json.dumps(
        [normalized_namespace, *identity_parts],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return f"{normalized_namespace}:{sha256(material.encode('utf-8')).hexdigest()}"


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
    "InvalidEffectTransition",
    "generated_effect_source_event_id",
    "transition_dispatch_attempt",
    "transition_runtime_intent",
    "transition_system_outbox",
]
