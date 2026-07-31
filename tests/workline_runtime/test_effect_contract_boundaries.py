"""EFFECT 状态合同与 reducer 的 fail-closed 边界。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.app.effect_ledger_status import SystemOutboxStatus
from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    InvalidEffectTransition,
    generated_effect_source_event_id,
    transition_system_outbox,
)
from src.app.runtime.orchestration.reconciliation_case import (
    ReconciliationCase,
    ReconciliationCaseStatus,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import (
    EffectReducer,
    ReconciliationEvidenceConflict,
)


def _event(
    event_type: EffectReducerEventType = EffectReducerEventType.RECONCILIATION_OPENED,
) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=event_type,
        dispatch_key="dispatch-1",
        occurred_at_ms=1,
        source_event_id="source-1",
        reason_code="TEST_REASON",
        evidence_json={"fact": "one"},
    )


def test_transition_rejects_unknown_current_or_target_status() -> None:
    with pytest.raises(InvalidEffectTransition, match="未知状态"):
        transition_system_outbox(
            SimpleNamespace(status="UNKNOWN_STATUS"),
            SystemOutboxStatus.SENT,
        )

    with pytest.raises(InvalidEffectTransition, match="未知状态"):
        transition_system_outbox(
            SimpleNamespace(status=SystemOutboxStatus.NEW),
            "UNKNOWN_TARGET",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("namespace", ("", "x" * 121))
def test_generated_source_event_identity_rejects_unbounded_namespace(namespace) -> None:
    with pytest.raises(ValueError, match=r"1\.\.120"):
        generated_effect_source_event_id(namespace, "dispatch-1")


@pytest.mark.parametrize(
    "overrides",
    (
        {"attempt_no": 1},
        {"resolution": RuntimeIntentStatus.COMPLETED},
        {"retry_exhausted": True},
    ),
)
def test_reducer_event_rejects_fields_owned_by_other_event_types(overrides) -> None:
    with pytest.raises(ValidationError):
        EffectReducerEvent(
            event_type=EffectReducerEventType.CALLBACK_ACCEPTED,
            dispatch_key="dispatch-1",
            occurred_at_ms=1,
            **overrides,
        )


def test_intent_evidence_replay_rejects_reused_identity_with_different_fact() -> None:
    event = _event(EffectReducerEventType.CALLBACK_ACCEPTED)
    evidence = {
        "event_type": event.event_type.value,
        "source_event_id": event.source_event_id,
        "reason_code": "NEW_REASON",
        "occurred_at_ms": 2,
    }
    intent = SimpleNamespace(
        outcome_history_json=[
            {
                **evidence,
                "reason_code": "OLD_REASON",
                "occurred_at_ms": 1,
            }
        ]
    )

    with pytest.raises(ReconciliationEvidenceConflict, match="different evidence"):
        EffectReducer._is_intent_evidence_replay(intent, event=event, evidence=evidence)


def test_new_reconciliation_case_requires_persisted_intent_id() -> None:
    with pytest.raises(TypeError, match=r"persisted RuntimeIntentLog\.id"):
        EffectReducer._new_case(
            SimpleNamespace(id=None),
            event=_event(),
            evidence={"event_type": "RECONCILIATION_OPENED"},
            contradiction=False,
        )


class _Repository:
    def __init__(self) -> None:
        self.intent = SimpleNamespace(
            id=1,
            dispatch_key="dispatch-1",
            effect_status=RuntimeIntentStatus.RECONCILING,
            outcome_kind=None,
            outcome_code=None,
            outcome_json={},
            outcome_history_json=[],
            effect_updated_at_ms=None,
            capability_key="test.effect",
            capability_contract_version="v1",
        )
        self.case = ReconciliationCase(
            runtime_intent_log_id=1,
            dispatch_key="dispatch-1",
            status=ReconciliationCaseStatus.OPEN,
            reason_code="FIRST_REASON",
            evidence_history_json=[],
            decision_json={},
            opened_at_ms=0,
        )

    async def get_intent_for_update(self, _db, _dispatch_key):
        return self.intent

    async def get_open_case_for_update(self, _db, _dispatch_key):
        return self.case

    async def list_resolved_cases_for_update(self, _db, _dispatch_key):
        return ()

    def add_case(self, _db, _case):
        raise AssertionError("existing OPEN case must be reused")


class _Db:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.asyncio
async def test_second_case_opening_event_appends_evidence_to_existing_open_case() -> None:
    repository = _Repository()
    db = _Db()

    result = await EffectReducer(repository=repository).reduce(db, _event())

    assert result.case_created is False
    assert len(repository.case.evidence_history_json) == 1
    assert db.flush_count == 1
