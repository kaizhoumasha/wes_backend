"""EFFECT 双账本状态、reducer event 与 1:1 派发键合同。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

try:
    from src.app.runtime.orchestration.effect_state_contract import (
        DISPATCH_ATTEMPT_TRANSITIONS,
        EFFECT_REDUCER_EVENT_TYPES,
        RUNTIME_INTENT_TRANSITIONS,
        SYSTEM_OUTBOX_TRANSITIONS,
        EffectReducerEvent,
        EffectReducerEventType,
        InvalidEffectTransition,
        transition_dispatch_attempt,
        transition_runtime_intent,
        transition_system_outbox,
    )
    from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
except ImportError as exc:
    _CONTRACT_IMPORT_ERROR: ImportError | None = exc
else:
    _CONTRACT_IMPORT_ERROR = None

from src.app.runtime.orchestration.models.dispatch_attempt import DispatchAttemptStatus
from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import RuntimeIntentLogRepository
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
    SystemOutboxUpdate,
)
from tests.support.external_http import frozen_external_http_binding


def _require_effect_state_contract() -> None:
    assert _CONTRACT_IMPORT_ERROR is None, f"EFFECT state contract 尚未实现: {_CONTRACT_IMPORT_ERROR}"


def _values(enum_type: type) -> set[str]:
    return {str(item.value) for item in enum_type}


def _unique_column_sets(table: Any) -> set[tuple[str, ...]]:
    indexes = table.indexes
    constraints = table.constraints
    unique_sets = {
        tuple(column.name for column in index.columns) for index in indexes if bool(getattr(index, "unique", False))
    }
    unique_sets.update(
        tuple(column.name for column in constraint.columns)
        for constraint in constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    )
    return unique_sets


def test_effect_ledgers_expose_only_final_state_enums() -> None:
    _require_effect_state_contract()
    assert _values(RuntimeIntentStatus) == {
        "PROPOSED",
        "ACCEPTED",
        "COMPLETED",
        "REJECTED",
        "TECHNICAL_FAILED",
        "UNKNOWN",
        "RECONCILING",
    }
    assert _values(SystemOutboxStatus) == {
        "NEW",
        "DISPATCHING",
        "RETRY_WAIT",
        "SENT",
        "FAILED",
        "UNKNOWN",
        "CANCELLED",
    }
    assert _values(DispatchAttemptStatus) == {
        "DISPATCHING",
        "SENT",
        "FAILED",
        "UNKNOWN",
        "CANCELLED",
    }


def test_effect_transition_matrices_are_closed_and_terminal_states_have_no_outgoing_edges() -> None:
    _require_effect_state_contract()
    assert {
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
    } == RUNTIME_INTENT_TRANSITIONS
    assert {
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
    } == SYSTEM_OUTBOX_TRANSITIONS
    assert {
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
    } == DISPATCH_ATTEMPT_TRANSITIONS


def test_effect_reducer_event_schema_is_closed_and_supports_reconciliation_resolution() -> None:
    _require_effect_state_contract()
    assert (
        frozenset(
            {
                EffectReducerEventType.INTENT_PROPOSED,
                EffectReducerEventType.ATTEMPT_STARTED,
                EffectReducerEventType.TRANSPORT_NOT_SENT,
                EffectReducerEventType.TRANSPORT_ACCEPTED,
                EffectReducerEventType.TRANSPORT_AMBIGUOUS,
                EffectReducerEventType.CALLBACK_ACCEPTED,
                EffectReducerEventType.CALLBACK_COMPLETED,
                EffectReducerEventType.CALLBACK_REJECTED,
                EffectReducerEventType.RECONCILIATION_OPENED,
                EffectReducerEventType.RECONCILIATION_RESOLVED,
                EffectReducerEventType.IDEMPOTENCY_CONFLICT,
            }
        )
        == EFFECT_REDUCER_EVENT_TYPES
    )
    resolved = EffectReducerEvent(
        event_type=EffectReducerEventType.RECONCILIATION_RESOLVED,
        dispatch_key="dispatch-1",
        occurred_at_ms=1000,
        resolution=RuntimeIntentStatus.COMPLETED,
        evidence_json={"case_id": 7},
    )
    assert resolved.resolution is RuntimeIntentStatus.COMPLETED

    with pytest.raises(ValueError, match="resolution"):
        EffectReducerEvent(
            event_type=EffectReducerEventType.RECONCILIATION_RESOLVED,
            dispatch_key="dispatch-1",
            occurred_at_ms=1000,
        )
    with pytest.raises(ValueError, match="attempt_no"):
        EffectReducerEvent(
            event_type=EffectReducerEventType.TRANSPORT_AMBIGUOUS,
            dispatch_key="dispatch-1",
            occurred_at_ms=1000,
        )


def test_transition_guard_accepts_matrix_edges_and_rejects_terminal_or_skipped_overwrites() -> None:
    _require_effect_state_contract()
    intent = SimpleNamespace(effect_status=RuntimeIntentStatus.PROPOSED)
    outbox = SimpleNamespace(status=SystemOutboxStatus.NEW)
    attempt = SimpleNamespace(status=DispatchAttemptStatus.DISPATCHING)

    transition_runtime_intent(intent, RuntimeIntentStatus.ACCEPTED)
    transition_system_outbox(outbox, SystemOutboxStatus.DISPATCHING)
    transition_dispatch_attempt(attempt, DispatchAttemptStatus.SENT)

    assert intent.effect_status is RuntimeIntentStatus.ACCEPTED
    assert outbox.status is SystemOutboxStatus.DISPATCHING
    assert attempt.status is DispatchAttemptStatus.SENT

    with pytest.raises(InvalidEffectTransition, match="SystemOutbox"):
        transition_system_outbox(SimpleNamespace(status=SystemOutboxStatus.NEW), SystemOutboxStatus.SENT)
    with pytest.raises(InvalidEffectTransition, match="RuntimeIntentLog"):
        transition_runtime_intent(
            SimpleNamespace(effect_status=RuntimeIntentStatus.COMPLETED),
            RuntimeIntentStatus.PROPOSED,
        )
    with pytest.raises(InvalidEffectTransition, match="DispatchAttempt"):
        transition_dispatch_attempt(attempt, DispatchAttemptStatus.FAILED)


def test_dispatch_key_is_excluded_from_update_schema() -> None:
    assert "dispatch_key" not in SystemOutboxUpdate.model_fields


def test_runtime_intent_log_owns_semantic_state_only_and_dispatch_keys_are_unique_on_both_ledgers() -> None:
    _require_effect_state_contract()
    runtime_fields = RuntimeIntentLog.model_fields
    assert runtime_fields["dispatch_key"].is_required()
    assert runtime_fields["effect_status"].default is RuntimeIntentStatus.PROPOSED
    assert {"dispatch_status", "attempt_count", "last_error_code", "last_error_message"}.isdisjoint(runtime_fields)
    assert ("dispatch_key",) in _unique_column_sets(RuntimeIntentLog.__table__)
    assert ("dispatch_key",) in _unique_column_sets(SystemOutbox.__table__)


class _PairSession:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self.flush_count = 0
        self.commit_count = 0

    def add_all(self, rows: list[object]) -> None:
        self.rows.extend(rows)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        self.commit_count += 1


def _intent_log(dispatch_key: str) -> RuntimeIntentLog:
    return RuntimeIntentLog(
        execution_session_id=1,
        correlation_id="corr-1",
        provider_code="WMS",
        operation_kind="system_capability_effect",
        target_domain="wms_integration",
        target_action="notify_pkg_binding",
        idempotency_key="effect-1",
        request_hash="a" * 64,
        dispatch_key=dispatch_key,
    )


def _outbox(dispatch_key: str) -> SystemOutbox:
    projection = {"request_id": dispatch_key}
    canonical = CanonicalPayload.from_projection(projection)
    frozen_binding = frozen_external_http_binding(
        target_code="WMS",
        provider_profile_identity="wms.profile-test",
        operation_identity="wms.effect-test@v1",
    )
    return SystemOutbox(
        **frozen_binding.as_persisted_fields(),
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        payload_json=projection,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
    )


@pytest.mark.asyncio
async def test_repository_adds_exactly_one_intent_and_outbox_with_same_dispatch_key_without_committing() -> None:
    _require_effect_state_contract()
    db = _PairSession()

    await RuntimeIntentLogRepository().add_proposed_pair(
        db,
        intent_log=_intent_log("dispatch-1"),
        outbox=_outbox("dispatch-1"),
    )

    assert [type(row) for row in db.rows] == [RuntimeIntentLog, SystemOutbox]
    assert {row.dispatch_key for row in db.rows} == {"dispatch-1"}
    assert db.flush_count == 1
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_repository_rejects_mismatched_dispatch_keys_before_writing_pair() -> None:
    _require_effect_state_contract()
    db = _PairSession()

    with pytest.raises(ValueError, match="dispatch_key"):
        await RuntimeIntentLogRepository().add_proposed_pair(
            db,
            intent_log=_intent_log("dispatch-1"),
            outbox=_outbox("dispatch-2"),
        )

    assert db.rows == []
    assert db.flush_count == 0
