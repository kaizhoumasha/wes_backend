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
from src.app.sys.models.outbox import WMS_ASYNC_EFFECT_OPERATION_IDENTITIES


class EffectIntentNotFound(LookupError):
    """Reducer event 无法关联权威 RuntimeIntentLog。"""


class InvalidReconciliationEvent(ValueError):
    """对账事件与当前 case 生命周期不匹配。"""


class ReconciliationResolutionConflict(InvalidReconciliationEvent):
    """同一人工决议请求身份被复用于不同裁决。"""


class ReconciliationEvidenceConflict(InvalidReconciliationEvent):
    """同一 evidence 身份被复用于不同事实。"""


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
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.ACCEPTED,
                # 模糊传输后的 status-first / 同键重提 ACK 会恢复同一权威 envelope。
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.ACCEPTED,
            }
        ),
        EffectReducerEventType.TRANSPORT_REJECTED: MappingProxyType(
            {RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.REJECTED}
        ),
        EffectReducerEventType.TRANSPORT_AMBIGUOUS: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.UNKNOWN,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.UNKNOWN,
            }
        ),
        EffectReducerEventType.SYNC_COMPLETED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.COMPLETED,
            }
        ),
        EffectReducerEventType.SYNC_REJECTED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.REJECTED,
            }
        ),
        EffectReducerEventType.LOCAL_REDECISION_REQUIRED: MappingProxyType({}),
        EffectReducerEventType.DISPATCH_CANCELLED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.RECONCILING,
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
        EffectReducerEventType.STATUS_ACCEPTED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.ACCEPTED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.ACCEPTED,
            }
        ),
        EffectReducerEventType.STATUS_PROCESSING: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.ACCEPTED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.ACCEPTED,
            }
        ),
        EffectReducerEventType.STATUS_COMPLETED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.COMPLETED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.COMPLETED,
            }
        ),
        EffectReducerEventType.STATUS_REJECTED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.REJECTED,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.REJECTED,
            }
        ),
        EffectReducerEventType.STATUS_NOT_FOUND: MappingProxyType({}),
        EffectReducerEventType.STATUS_QUERY_FAILED: MappingProxyType({}),
        EffectReducerEventType.STATUS_STALE: MappingProxyType({}),
        EffectReducerEventType.RECONCILIATION_OPENED: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.RECONCILING,
            }
        ),
        EffectReducerEventType.RECONCILIATION_RESOLVED: MappingProxyType({}),
        EffectReducerEventType.IDEMPOTENCY_CONFLICT: MappingProxyType(
            {
                RuntimeIntentStatus.PROPOSED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.ACCEPTED: RuntimeIntentStatus.RECONCILING,
                RuntimeIntentStatus.UNKNOWN: RuntimeIntentStatus.RECONCILING,
            }
        ),
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
_STATUS_TERMINAL_EVENTS = frozenset(
    {
        EffectReducerEventType.STATUS_COMPLETED,
        EffectReducerEventType.STATUS_REJECTED,
        EffectReducerEventType.SYNC_COMPLETED,
        EffectReducerEventType.SYNC_REJECTED,
    }
)
_CASE_OPENING_EVENTS = frozenset(
    {
        EffectReducerEventType.RECONCILIATION_OPENED,
        EffectReducerEventType.IDEMPOTENCY_CONFLICT,
        EffectReducerEventType.DISPATCH_CANCELLED,
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
        evidence = self._serialize_evidence(event)
        if (
            open_case is not None
            and event.source_event_id
            and self._is_case_evidence_replay(
                open_case,
                event=event,
                evidence=evidence,
            )
        ):
            return EffectReductionResult(
                intent_status=current,
                case_status=ReconciliationCaseStatus.OPEN,
                state_changed=False,
                case_created=False,
                contradiction=False,
            )
        if event.source_event_id and event.event_type is not EffectReducerEventType.RECONCILIATION_RESOLVED:
            resolved_cases = await self._repository.list_resolved_cases_for_update(db, event.dispatch_key)
            if any(self._is_case_evidence_replay(case, event=event, evidence=evidence) for case in resolved_cases):
                return EffectReductionResult(
                    intent_status=current,
                    case_status=(
                        ReconciliationCaseStatus.RESOLVED if open_case is None else ReconciliationCaseStatus.OPEN
                    ),
                    state_changed=False,
                    case_created=False,
                    contradiction=False,
                )
        if (
            event.source_event_id
            and event.event_type is not EffectReducerEventType.RECONCILIATION_RESOLVED
            and self._is_intent_evidence_replay(intent, event=event, evidence=evidence)
        ):
            return EffectReductionResult(
                intent_status=current,
                case_status=open_case.status if open_case is not None else None,
                state_changed=False,
                case_created=False,
                contradiction=False,
            )
        contradiction = self._is_contradictory_evidence(intent, current=current, event_type=event.event_type)
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
            status_query_authoritative=(
                f"{getattr(intent, 'capability_key', '')}@"
                f"{getattr(intent, 'capability_contract_version', '')}" in WMS_ASYNC_EFFECT_OPERATION_IDENTITIES
            ),
        )
        if target is not None and target is not current:
            transition_runtime_intent(intent, target)
            self._write_current_outcome(intent, event=event)
            state_changed = True
        else:
            state_changed = False
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
    def _is_case_evidence_replay(
        case: ReconciliationCase,
        *,
        event: EffectReducerEvent,
        evidence: dict[str, object],
    ) -> bool:
        matching = next(
            (
                existing
                for existing in case.evidence_history_json
                if existing.get("source_event_id") == event.source_event_id
                and existing.get("event_type") == event.event_type.value
            ),
            None,
        )
        if matching is None:
            return False
        existing_fact = {key: value for key, value in matching.items() if key != "occurred_at_ms"}
        replayed_fact = {key: value for key, value in evidence.items() if key != "occurred_at_ms"}
        if existing_fact != replayed_fact:
            raise ReconciliationEvidenceConflict("source_event_id cannot be reused with different evidence")
        return True

    @staticmethod
    def _is_intent_evidence_replay(
        intent: Any,
        *,
        event: EffectReducerEvent,
        evidence: dict[str, object],
    ) -> bool:
        matching = next(
            (
                existing
                for existing in (intent.outcome_history_json or ())
                if existing.get("source_event_id") == event.source_event_id
                and existing.get("event_type") == event.event_type.value
            ),
            None,
        )
        if matching is None:
            return False
        existing_fact = {key: value for key, value in matching.items() if key != "occurred_at_ms"}
        replayed_fact = {key: value for key, value in evidence.items() if key != "occurred_at_ms"}
        if existing_fact != replayed_fact:
            raise ReconciliationEvidenceConflict("source_event_id cannot be reused with different evidence")
        return True

    @staticmethod
    def _target_status(
        *,
        current: RuntimeIntentStatus,
        event: EffectReducerEvent,
        has_open_case: bool,
        status_query_authoritative: bool,
    ) -> RuntimeIntentStatus | None:
        if current in _TERMINAL_STATUSES:
            return None
        if event.event_type is EffectReducerEventType.RECONCILIATION_RESOLVED:
            return event.resolution if current is RuntimeIntentStatus.RECONCILING else None
        if has_open_case and event.event_type not in _CASE_OPENING_EVENTS:
            return None
        if status_query_authoritative and event.event_type in {
            EffectReducerEventType.CALLBACK_COMPLETED,
            EffectReducerEventType.CALLBACK_REJECTED,
        }:
            # WMS EFFECT callback 只作为旁证；
            # 业务终态必须来自 lease-fenced typed status snapshot。
            return None
        if (
            event.event_type is EffectReducerEventType.TRANSPORT_NOT_SENT
            and event.retry_exhausted
            and current is RuntimeIntentStatus.PROPOSED
        ):
            return RuntimeIntentStatus.TECHNICAL_FAILED
        return EFFECT_REDUCER_TRANSITION_MATRIX[event.event_type].get(current)

    @staticmethod
    def _is_contradictory_evidence(
        intent: Any,
        *,
        current: RuntimeIntentStatus,
        event_type: EffectReducerEventType,
    ) -> bool:
        if event_type in _STATUS_TERMINAL_EVENTS:
            return (current, event_type) in {
                (RuntimeIntentStatus.COMPLETED, EffectReducerEventType.STATUS_REJECTED),
                (RuntimeIntentStatus.REJECTED, EffectReducerEventType.STATUS_COMPLETED),
                (RuntimeIntentStatus.TECHNICAL_FAILED, EffectReducerEventType.STATUS_COMPLETED),
                (RuntimeIntentStatus.TECHNICAL_FAILED, EffectReducerEventType.STATUS_REJECTED),
                (RuntimeIntentStatus.COMPLETED, EffectReducerEventType.SYNC_REJECTED),
                (RuntimeIntentStatus.REJECTED, EffectReducerEventType.SYNC_COMPLETED),
                (RuntimeIntentStatus.TECHNICAL_FAILED, EffectReducerEventType.SYNC_COMPLETED),
                (RuntimeIntentStatus.TECHNICAL_FAILED, EffectReducerEventType.SYNC_REJECTED),
            }
        if event_type in _CALLBACK_EVENTS:
            if current is RuntimeIntentStatus.TECHNICAL_FAILED:
                return True
            return (current, event_type) in {
                (RuntimeIntentStatus.COMPLETED, EffectReducerEventType.CALLBACK_REJECTED),
                (RuntimeIntentStatus.REJECTED, EffectReducerEventType.CALLBACK_COMPLETED),
            }
        if current is RuntimeIntentStatus.REJECTED and event_type is EffectReducerEventType.TRANSPORT_ACCEPTED:
            # 2xx 只证明请求已送达；若终态来自业务 callback 拒绝或人工决议，它与该事实并不冲突。
            return any(
                evidence.get("event_type") == EffectReducerEventType.TRANSPORT_REJECTED.value
                for evidence in (intent.outcome_history_json or ())
            )
        return (current, event_type) in {
            (RuntimeIntentStatus.ACCEPTED, EffectReducerEventType.TRANSPORT_REJECTED),
            (RuntimeIntentStatus.COMPLETED, EffectReducerEventType.TRANSPORT_REJECTED),
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
        outcome_kind = str(evidence.get("outcome_kind") or event.event_type.value.lower())
        outcome_code = str(evidence.get("outcome_code") or event.reason_code or event.event_type.value)
        intent.outcome_kind = outcome_kind
        intent.outcome_code = outcome_code
        if event.terminal_outcome is None:
            intent.outcome_json = evidence
            return
        intent.outcome_json = {
            "capability_key": str(getattr(intent, "capability_key", "") or ""),
            "contract_version": str(getattr(intent, "capability_contract_version", "") or ""),
            "operation_key": str(getattr(intent, "operation_identity", "") or ""),
            "idempotency_key": str(getattr(intent, "idempotency_key", "") or ""),
            "payload_hash": str(getattr(intent, "payload_hash", None) or getattr(intent, "request_hash", "") or ""),
            "outcome_kind": outcome_kind,
            "outcome_code": outcome_code,
            "outcome": dict(event.terminal_outcome),
            "occurred_at_ms": event.occurred_at_ms,
        }

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
    "ReconciliationEvidenceConflict",
    "ReconciliationResolutionConflict",
    "effect_reducer",
]
