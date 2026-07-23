"""Capability EFFECT 唯一语义状态 reducer。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    transition_runtime_intent,
)
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.repositories.effect_reducer_repository import (
    EffectReducerRepository,
    effect_reducer_repository,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus


class EffectIntentNotFound(LookupError):
    """Reducer event 无法关联权威 RuntimeIntentLog。"""


class InvalidReconciliationEvent(ValueError):
    """对账事件与当前 case 生命周期不匹配。"""


class ReconciliationResolutionConflict(InvalidReconciliationEvent):
    """同一人工决议请求身份被复用于不同裁决。"""


@dataclass(frozen=True, slots=True)
class EffectReductionResult:
    """单次 reducer 归并结果。"""

    intent_status: RuntimeIntentStatus
    case_status: ReconciliationCaseStatus | None
    state_changed: bool
    case_created: bool
    contradiction: bool


EFFECT_REDUCER_TRANSITION_MATRIX = MappingProxyType(
    {
        EffectReducerEventType.INTENT_PROPOSED: MappingProxyType({}),
        EffectReducerEventType.ATTEMPT_STARTED: MappingProxyType({}),
        EffectReducerEventType.TRANSPORT_NOT_SENT: MappingProxyType({}),
        EffectReducerEventType.TRANSPORT_ACCEPTED: MappingProxyType(
            {RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.ACCEPTED}
        ),
        EffectReducerEventType.TRANSPORT_AMBIGUOUS: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.UNKNOWN,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.UNKNOWN,
            }
        ),
        EffectReducerEventType.CALLBACK_ACCEPTED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.ACCEPTED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.ACCEPTED,
            }
        ),
        EffectReducerEventType.CALLBACK_COMPLETED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.COMPLETED,
            }
        ),
        EffectReducerEventType.CALLBACK_REJECTED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.REJECTED,
            }
        ),
        EffectReducerEventType.RECONCILIATION_OPENED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.RECONCILING,
            }
        ),
        EffectReducerEventType.RECONCILIATION_RESOLVED: MappingProxyType({}),
        EffectReducerEventType.IDEMPOTENCY_CONFLICT: MappingProxyType({}),
    }
)

_TERMINAL_STATUSES = frozenset(
    {
        RuntimeIntentStatus.COMPLETED,
        RuntimeIntentStatus.REJECTED,
        RuntimeIntentStatus.TECHNICAL_FAILED,
    }
)
_CALLBACK_EVENTS = frozenset(
    {
        EffectReducerEventType.CALLBACK_ACCEPTED,
        EffectReducerEventType.CALLBACK_COMPLETED,
        EffectReducerEventType.CALLBACK_REJECTED,
    }
)
_CASE_OPENING_EVENTS = frozenset(
    {
        EffectReducerEventType.RECONCILIATION_OPENED,
        EffectReducerEventType.IDEMPOTENCY_CONFLICT,
    }
)


class EffectReducer:
    """锁定 intent/case 后按封闭事件表单调归并。"""

    def __init__(self, *, repository: EffectReducerRepository = effect_reducer_repository) -> None:
        self._repository = repository

    async def reduce(
        self,
        db: Any,
        event: EffectReducerEvent,
        *,
        require_intent: bool = True,
    ) -> EffectReductionResult | None:
        intent = await self._repository.get_intent_for_update(db, event.dispatch_key)
        if intent is None:
            if require_intent:
                raise EffectIntentNotFound(f"RuntimeIntentLog dispatch_key={event.dispatch_key!r} 不存在")
            return None
        open_case = await self._repository.get_open_case_for_update(db, event.dispatch_key)
        current = RuntimeIntentStatus(intent.effect_status)
        contradiction = self._is_contradictory_callback(current, event.event_type)
        evidence = self._serialize_evidence(event)
        case_created = False

        if event.event_type is EffectReducerEventType.RECONCILIATION_RESOLVED:
            resolved_cases = await self._repository.list_resolved_cases_for_update(db, event.dispatch_key)
            if any(self._is_same_resolution_replay(case, event) for case in resolved_cases):
                return EffectReductionResult(
                    intent_status=current,
                    case_status=(
                        ReconciliationCaseStatus.RESOLVED if open_case is None else ReconciliationCaseStatus.OPEN
                    ),
                    state_changed=False,
                    case_created=False,
                    contradiction=False,
                )
            if open_case is None:
                raise InvalidReconciliationEvent("RECONCILIATION_RESOLVED requires an OPEN case")
            self._resolve_case(open_case, event=event, evidence=evidence)
        elif event.event_type in _CASE_OPENING_EVENTS or contradiction:
            if open_case is None:
                open_case = self._new_case(intent, event=event, evidence=evidence, contradiction=contradiction)
                self._repository.add_case(db, open_case)
                case_created = True
            else:
                self._append_case_evidence(open_case, evidence)
        elif open_case is not None:
            # OPEN case 下普通 callback/transport 只能追加事实，不能暗中关闭或推进 intent。
            self._append_case_evidence(open_case, evidence)

        target = self._target_status(
            current=current,
            event=event,
            has_open_case=open_case is not None,
        )
        state_changed = target is not None and target is not current
        if state_changed:
            transition_runtime_intent(intent, target)
            self._write_current_outcome(intent, event=event)
        self._append_intent_evidence(intent, event=event, evidence=evidence)
        await db.flush()
        return EffectReductionResult(
            intent_status=RuntimeIntentStatus(intent.effect_status),
            case_status=open_case.status if open_case is not None else None,
            state_changed=state_changed,
            case_created=case_created,
            contradiction=contradiction,
        )

    @staticmethod
    def _is_same_resolution_replay(case: ReconciliationCase | None, event: EffectReducerEvent) -> bool:
        if case is None or not event.source_event_id:
            return False
        decision = dict(case.decision_json or {})
        if not decision.get("source_event_id") or decision.get("source_event_id") != event.source_event_id:
            return False
        resolution = event.resolution.value if event.resolution is not None else None
        if decision.get("resolution") != resolution:
            raise ReconciliationResolutionConflict("source_event_id cannot be reused with a different resolution")
        return True

    @staticmethod
    def _target_status(
        *,
        current: RuntimeIntentStatus,
        event: EffectReducerEvent,
        has_open_case: bool,
    ) -> RuntimeIntentStatus | None:
        if current in _TERMINAL_STATUSES:
            return None
        if event.event_type is EffectReducerEventType.RECONCILIATION_RESOLVED:
            return event.resolution if current is RuntimeIntentStatus.RECONCILING else None
        if has_open_case and event.event_type not in _CASE_OPENING_EVENTS:
            return None
        if (
            event.event_type is EffectReducerEventType.TRANSPORT_NOT_SENT
            and event.retry_exhausted
            and current is RuntimeIntentStatus.PROPOSED
        ):
            return RuntimeIntentStatus.TECHNICAL_FAILED
        return EFFECT_REDUCER_TRANSITION_MATRIX[event.event_type].get(current)

    @staticmethod
    def _is_contradictory_callback(
        current: RuntimeIntentStatus,
        event_type: EffectReducerEventType,
    ) -> bool:
        if event_type not in _CALLBACK_EVENTS:
            return False
        if current is RuntimeIntentStatus.TECHNICAL_FAILED:
            return True
        return (current, event_type) in {
            (RuntimeIntentStatus.COMPLETED, EffectReducerEventType.CALLBACK_REJECTED),
            (RuntimeIntentStatus.REJECTED, EffectReducerEventType.CALLBACK_COMPLETED),
        }

    @staticmethod
    def _serialize_evidence(event: EffectReducerEvent) -> dict[str, object]:
        return {
            **dict(event.evidence_json),
            "event_type": event.event_type.value,
            "dispatch_key": event.dispatch_key,
            "occurred_at_ms": event.occurred_at_ms,
            "source_event_id": event.source_event_id,
            "attempt_no": event.attempt_no,
            "retry_exhausted": event.retry_exhausted,
            "resolution": event.resolution.value if event.resolution is not None else None,
            "reason_code": event.reason_code,
        }

    @staticmethod
    def _append_intent_evidence(intent: Any, *, event: EffectReducerEvent, evidence: dict[str, object]) -> None:
        intent.outcome_history_json = [*list(intent.outcome_history_json or []), evidence]
        current_updated_at = getattr(intent, "effect_updated_at_ms", None)
        intent.effect_updated_at_ms = max(current_updated_at or 0, event.occurred_at_ms)

    @staticmethod
    def _write_current_outcome(intent: Any, *, event: EffectReducerEvent) -> None:
        evidence = dict(event.evidence_json)
        intent.outcome_kind = str(evidence.get("outcome_kind") or event.event_type.value.lower())
        intent.outcome_code = str(evidence.get("outcome_code") or event.reason_code or event.event_type.value)
        intent.outcome_json = evidence

    @staticmethod
    def _append_case_evidence(case: ReconciliationCase, evidence: dict[str, object]) -> None:
        case.evidence_history_json = [*list(case.evidence_history_json or []), evidence]

    @classmethod
    def _new_case(
        cls,
        intent: Any,
        *,
        event: EffectReducerEvent,
        evidence: dict[str, object],
        contradiction: bool,
    ) -> ReconciliationCase:
        intent_id = getattr(intent, "id", None)
        if not isinstance(intent_id, int):
            raise TypeError("ReconciliationCase requires persisted RuntimeIntentLog.id")
        reason_code = event.reason_code or ("CONTRADICTORY_CALLBACK" if contradiction else event.event_type.value)
        return ReconciliationCase(
            runtime_intent_log_id=intent_id,
            dispatch_key=event.dispatch_key,
            status=ReconciliationCaseStatus.OPEN,
            reason_code=reason_code,
            evidence_history_json=[evidence],
            decision_json={},
            opened_at_ms=event.occurred_at_ms,
        )

    @classmethod
    def _resolve_case(
        cls,
        case: ReconciliationCase,
        *,
        event: EffectReducerEvent,
        evidence: dict[str, object],
    ) -> None:
        cls._append_case_evidence(case, evidence)
        case.status = ReconciliationCaseStatus.RESOLVED
        case.decision_json = {
            "source_event_id": event.source_event_id,
            "resolution": event.resolution.value if event.resolution is not None else None,
            "reason_code": event.reason_code,
            "evidence": dict(event.evidence_json),
        }
        case.resolved_at_ms = event.occurred_at_ms


effect_reducer = EffectReducer()


__all__ = [
    "EFFECT_REDUCER_TRANSITION_MATRIX",
    "EffectIntentNotFound",
    "EffectReducer",
    "EffectReductionResult",
    "InvalidReconciliationEvent",
    "ReconciliationResolutionConflict",
    "effect_reducer",
]
