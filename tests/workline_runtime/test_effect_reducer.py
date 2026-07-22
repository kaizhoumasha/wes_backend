"""T8d EFFECT reducer、对账 case 与 typed bridge 合同。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
    SystemCapabilityIntentService,
)
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)

try:
    from src.app.runtime.orchestration.effect_bridges import (
        EffectCallbackBridge,
        EffectCallbackOutcome,
        EffectReconciliationBridge,
        EffectTransportBridge,
    )
    from src.app.runtime.orchestration.reconciliation_case import (
        ReconciliationCase,
        ReconciliationCaseStatus,
    )
    from src.app.runtime.orchestration.services.effect_reducer_service import (
        EffectIntentNotFound,
        EffectReducer,
        InvalidReconciliationEvent,
    )
except ImportError as exc:
    _T8D_IMPORT_ERROR: ImportError | None = exc
else:
    _T8D_IMPORT_ERROR = None


def _require_t8d() -> None:
    assert _T8D_IMPORT_ERROR is None, f"T8d reducer 尚未实现: {_T8D_IMPORT_ERROR}"


class _Db:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1


class _ReducerRepository:
    def __init__(self, status: RuntimeIntentStatus = RuntimeIntentStatus.PROPOSED) -> None:
        self.intent = SimpleNamespace(
            id=17,
            dispatch_key="dispatch-1",
            effect_status=status,
            outcome_kind=None,
            outcome_code=None,
            outcome_json={},
            outcome_history_json=[],
            effect_updated_at_ms=None,
        )
        self.cases: list[Any] = []

    async def get_intent_for_update(self, _db: Any, dispatch_key: str) -> Any | None:
        return self.intent if dispatch_key == self.intent.dispatch_key else None

    async def get_open_case_for_update(self, _db: Any, dispatch_key: str) -> Any | None:
        return next(
            (
                case
                for case in reversed(self.cases)
                if case.dispatch_key == dispatch_key and case.status is ReconciliationCaseStatus.OPEN
            ),
            None,
        )

    def add_case(self, _db: Any, case: Any) -> None:
        self.cases.append(case)


def _event(
    event_type: EffectReducerEventType,
    *,
    occurred_at_ms: int = 1000,
    retry_exhausted: bool = False,
    resolution: RuntimeIntentStatus | None = None,
    reason_code: str | None = None,
) -> EffectReducerEvent:
    kwargs: dict[str, Any] = {
        "event_type": event_type,
        "dispatch_key": "dispatch-1",
        "occurred_at_ms": occurred_at_ms,
        "source_event_id": f"source:{event_type.value}:{occurred_at_ms}",
        "retry_exhausted": retry_exhausted,
        "resolution": resolution,
        "reason_code": reason_code,
        "evidence_json": {"fact": event_type.value},
    }
    if event_type in {
        EffectReducerEventType.ATTEMPT_STARTED,
        EffectReducerEventType.TRANSPORT_NOT_SENT,
        EffectReducerEventType.TRANSPORT_ACCEPTED,
        EffectReducerEventType.TRANSPORT_AMBIGUOUS,
    }:
        kwargs["attempt_no"] = 1
    return EffectReducerEvent(**kwargs)


@pytest.mark.parametrize(
    ("start", "event_type", "retry_exhausted", "expected"),
    [
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.INTENT_PROPOSED, False, RuntimeIntentStatus.PROPOSED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.ATTEMPT_STARTED, False, RuntimeIntentStatus.PROPOSED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.TRANSPORT_NOT_SENT, False, RuntimeIntentStatus.PROPOSED),
        (
            RuntimeIntentStatus.PROPOSED,
            EffectReducerEventType.TRANSPORT_NOT_SENT,
            True,
            RuntimeIntentStatus.TECHNICAL_FAILED,
        ),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.TRANSPORT_ACCEPTED, False, RuntimeIntentStatus.ACCEPTED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.TRANSPORT_AMBIGUOUS, False, RuntimeIntentStatus.UNKNOWN),
        (RuntimeIntentStatus.ACCEPTED, EffectReducerEventType.TRANSPORT_AMBIGUOUS, False, RuntimeIntentStatus.UNKNOWN),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.CALLBACK_ACCEPTED, False, RuntimeIntentStatus.ACCEPTED),
        (RuntimeIntentStatus.UNKNOWN, EffectReducerEventType.CALLBACK_ACCEPTED, False, RuntimeIntentStatus.ACCEPTED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.CALLBACK_COMPLETED, False, RuntimeIntentStatus.COMPLETED),
        (RuntimeIntentStatus.ACCEPTED, EffectReducerEventType.CALLBACK_COMPLETED, False, RuntimeIntentStatus.COMPLETED),
        (RuntimeIntentStatus.UNKNOWN, EffectReducerEventType.CALLBACK_COMPLETED, False, RuntimeIntentStatus.COMPLETED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.CALLBACK_REJECTED, False, RuntimeIntentStatus.REJECTED),
        (RuntimeIntentStatus.ACCEPTED, EffectReducerEventType.CALLBACK_REJECTED, False, RuntimeIntentStatus.REJECTED),
        (RuntimeIntentStatus.UNKNOWN, EffectReducerEventType.CALLBACK_REJECTED, False, RuntimeIntentStatus.REJECTED),
        (
            RuntimeIntentStatus.ACCEPTED,
            EffectReducerEventType.RECONCILIATION_OPENED,
            False,
            RuntimeIntentStatus.RECONCILING,
        ),
        (
            RuntimeIntentStatus.UNKNOWN,
            EffectReducerEventType.RECONCILIATION_OPENED,
            False,
            RuntimeIntentStatus.RECONCILING,
        ),
    ],
)
@pytest.mark.asyncio
async def test_effect_reducer_follows_the_closed_table(
    start: RuntimeIntentStatus,
    event_type: EffectReducerEventType,
    retry_exhausted: bool,
    expected: RuntimeIntentStatus,
) -> None:
    _require_t8d()
    repository = _ReducerRepository(start)
    db = _Db()

    result = await EffectReducer(repository=repository).reduce(
        db,
        _event(event_type, retry_exhausted=retry_exhausted),
    )

    assert result is not None
    assert repository.intent.effect_status is expected
    assert result.intent_status is expected
    assert len(repository.intent.outcome_history_json) == 1
    assert repository.intent.outcome_history_json[0]["event_type"] == event_type.value
    assert db.flush_count == 1


@pytest.mark.parametrize("terminal", list(RuntimeIntentStatus)[2:5])
@pytest.mark.parametrize("event_type", list(EffectReducerEventType))
@pytest.mark.asyncio
async def test_effect_reducer_never_rewrites_terminal_intent(
    terminal: RuntimeIntentStatus,
    event_type: EffectReducerEventType,
) -> None:
    _require_t8d()
    repository = _ReducerRepository(terminal)
    if event_type is EffectReducerEventType.RECONCILIATION_RESOLVED:
        repository.cases.append(
            ReconciliationCase(
                runtime_intent_log_id=17,
                dispatch_key="dispatch-1",
                status=ReconciliationCaseStatus.OPEN,
                reason_code="MANUAL_REVIEW",
                evidence_history_json=[],
                decision_json={},
                opened_at_ms=900,
            )
        )

    await EffectReducer(repository=repository).reduce(
        _Db(),
        _event(
            event_type,
            retry_exhausted=event_type is EffectReducerEventType.TRANSPORT_NOT_SENT,
            resolution=(
                RuntimeIntentStatus.COMPLETED if event_type is EffectReducerEventType.RECONCILIATION_RESOLVED else None
            ),
        ),
    )

    assert repository.intent.effect_status is terminal


@pytest.mark.asyncio
async def test_callback_before_transport_response_keeps_completed_terminal() -> None:
    _require_t8d()
    repository = _ReducerRepository()
    reducer = EffectReducer(repository=repository)

    await reducer.reduce(_Db(), _event(EffectReducerEventType.CALLBACK_COMPLETED, occurred_at_ms=1100))
    await reducer.reduce(_Db(), _event(EffectReducerEventType.TRANSPORT_ACCEPTED, occurred_at_ms=900))

    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert repository.intent.effect_updated_at_ms == 1100
    assert [item["event_type"] for item in repository.intent.outcome_history_json] == [
        EffectReducerEventType.CALLBACK_COMPLETED.value,
        EffectReducerEventType.TRANSPORT_ACCEPTED.value,
    ]


@pytest.mark.asyncio
async def test_duplicate_and_contradictory_callbacks_append_evidence_without_terminal_overwrite() -> None:
    _require_t8d()
    repository = _ReducerRepository()
    reducer = EffectReducer(repository=repository)

    await reducer.reduce(_Db(), _event(EffectReducerEventType.CALLBACK_COMPLETED, occurred_at_ms=1000))
    await reducer.reduce(_Db(), _event(EffectReducerEventType.CALLBACK_COMPLETED, occurred_at_ms=1001))
    contradiction = await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.CALLBACK_REJECTED, occurred_at_ms=1002),
    )

    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert len(repository.intent.outcome_history_json) == 3
    assert len(repository.cases) == 1
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN
    assert contradiction is not None and contradiction.contradiction is True


@pytest.mark.asyncio
async def test_open_case_blocks_ordinary_callback_until_explicit_resolution() -> None:
    _require_t8d()
    repository = _ReducerRepository(RuntimeIntentStatus.ACCEPTED)
    reducer = EffectReducer(repository=repository)

    await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.RECONCILIATION_OPENED, reason_code="TRANSPORT_AMBIGUOUS"),
    )
    await reducer.reduce(_Db(), _event(EffectReducerEventType.CALLBACK_COMPLETED, occurred_at_ms=1100))

    assert repository.intent.effect_status is RuntimeIntentStatus.RECONCILING
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN
    assert len(repository.cases[0].evidence_history_json) == 2

    await reducer.reduce(
        _Db(),
        _event(
            EffectReducerEventType.RECONCILIATION_RESOLVED,
            occurred_at_ms=1200,
            resolution=RuntimeIntentStatus.COMPLETED,
            reason_code="REMOTE_CONFIRMED",
        ),
    )

    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert repository.cases[0].status is ReconciliationCaseStatus.RESOLVED
    assert repository.cases[0].decision_json["resolution"] == RuntimeIntentStatus.COMPLETED.value
    assert repository.cases[0].resolved_at_ms == 1200


@pytest.mark.asyncio
async def test_reconciliation_resolution_requires_an_open_case() -> None:
    _require_t8d()

    with pytest.raises(InvalidReconciliationEvent, match="OPEN"):
        await EffectReducer(repository=_ReducerRepository(RuntimeIntentStatus.RECONCILING)).reduce(
            _Db(),
            _event(
                EffectReducerEventType.RECONCILIATION_RESOLVED,
                resolution=RuntimeIntentStatus.COMPLETED,
            ),
        )


@pytest.mark.asyncio
async def test_missing_intent_is_strict_for_callback_but_optional_for_generic_transport() -> None:
    _require_t8d()
    repository = _ReducerRepository()
    reducer = EffectReducer(repository=repository)
    missing = _event(EffectReducerEventType.CALLBACK_COMPLETED).model_copy(update={"dispatch_key": "missing"})

    with pytest.raises(EffectIntentNotFound, match="missing"):
        await reducer.reduce(_Db(), missing)
    assert await reducer.reduce(_Db(), missing, require_intent=False) is None


def test_reconciliation_case_is_runtime_owned_and_has_no_cross_schema_foreign_key() -> None:
    _require_t8d()
    table = ReconciliationCase.__table__

    assert table.schema == "wes_runtime"
    assert {item.value for item in ReconciliationCaseStatus} == {"OPEN", "RESOLVED"}
    assert {foreign_key.target_fullname.split(".", maxsplit=1)[0] for foreign_key in table.foreign_keys} <= {
        "wes_runtime"
    }
    assert any(index.name == "ux_reconciliation_cases_open_dispatch_key" and index.unique for index in table.indexes)


class _RecordingReducer:
    def __init__(self) -> None:
        self.events: list[EffectReducerEvent] = []
        self.require_intent: list[bool] = []

    async def reduce(
        self,
        _db: Any,
        event: EffectReducerEvent,
        *,
        require_intent: bool = True,
    ) -> SimpleNamespace:
        self.events.append(event)
        self.require_intent.append(require_intent)
        return SimpleNamespace(intent_status=RuntimeIntentStatus.PROPOSED)


@pytest.mark.parametrize(
    ("transport_result", "retry_exhausted", "expected_events"),
    [
        (
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.CONNECTING,
                safe_to_retry=True,
                error_code="CONNECT_ERROR",
            ),
            False,
            [EffectReducerEventType.TRANSPORT_NOT_SENT],
        ),
        (
            ExternalHttpTransportResult.accepted(
                http_status_code=202,
                protocol_result=ExternalHttpProtocolResult.ACCEPTED,
            ),
            False,
            [EffectReducerEventType.TRANSPORT_ACCEPTED],
        ),
        (
            ExternalHttpTransportResult.ambiguous(
                phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
                error_code="READ_TIMEOUT",
            ),
            False,
            [
                EffectReducerEventType.TRANSPORT_AMBIGUOUS,
                EffectReducerEventType.RECONCILIATION_OPENED,
            ],
        ),
    ],
)
@pytest.mark.asyncio
async def test_transport_bridge_maps_typed_result_without_marking_sent_as_completed(
    transport_result: ExternalHttpTransportResult,
    retry_exhausted: bool,
    expected_events: list[EffectReducerEventType],
) -> None:
    _require_t8d()
    reducer = _RecordingReducer()

    await EffectTransportBridge(reducer=reducer).record_result(
        _Db(),
        dispatch_key="dispatch-1",
        attempt_no=2,
        result=transport_result,
        retry_exhausted=retry_exhausted,
        occurred_at_ms=1300,
    )

    assert [event.event_type for event in reducer.events] == expected_events
    assert EffectReducerEventType.CALLBACK_COMPLETED not in expected_events
    assert reducer.require_intent == [False] * len(expected_events)


@pytest.mark.asyncio
async def test_callback_and_reconciliation_bridges_accept_only_typed_semantic_events() -> None:
    _require_t8d()
    reducer = _RecordingReducer()
    callback_bridge = EffectCallbackBridge(reducer=reducer)
    reconciliation_bridge = EffectReconciliationBridge(reducer=reducer)

    await callback_bridge.record(
        _Db(),
        dispatch_key="dispatch-1",
        outcome=EffectCallbackOutcome.COMPLETED,
        occurred_at_ms=1400,
        source_event_id="callback-1",
        evidence_json={"remote_status": "DONE"},
    )
    await reconciliation_bridge.open(
        _Db(),
        dispatch_key="dispatch-1",
        occurred_at_ms=1500,
        reason_code="MANUAL_REVIEW",
        evidence_json={"operator": "qa"},
    )
    await reconciliation_bridge.resolve(
        _Db(),
        dispatch_key="dispatch-1",
        occurred_at_ms=1600,
        resolution=RuntimeIntentStatus.REJECTED,
        reason_code="REMOTE_REJECTED",
        evidence_json={"ticket": "CASE-1"},
    )

    assert [event.event_type for event in reducer.events] == [
        EffectReducerEventType.CALLBACK_COMPLETED,
        EffectReducerEventType.RECONCILIATION_OPENED,
        EffectReducerEventType.RECONCILIATION_RESOLVED,
    ]
    assert reducer.require_intent == [True, True, True]
    assert reducer.events[-1].resolution is RuntimeIntentStatus.REJECTED


@pytest.mark.asyncio
async def test_reconciliation_bridge_maps_idempotency_conflict_to_closed_reducer_event() -> None:
    _require_t8d()
    reducer = _RecordingReducer()

    await EffectReconciliationBridge(reducer=reducer).record_idempotency_conflict(
        _Db(),
        dispatch_key="authoritative-dispatch",
        occurred_at_ms=1700,
        source_event_id="idempotency-conflict:stable",
        evidence_json={"idempotency_key": "stable-key", "incoming_request_hash": "b" * 64},
    )

    assert len(reducer.events) == 1
    event = reducer.events[0]
    assert event.event_type is EffectReducerEventType.IDEMPOTENCY_CONFLICT
    assert event.dispatch_key == "authoritative-dispatch"
    assert event.reason_code == "IDEMPOTENCY_CONFLICT"
    assert reducer.require_intent == [True]


def test_transport_outcome_enum_remains_the_t8c_closed_set() -> None:
    assert {item.value for item in ExternalHttpTransportOutcome} == {"NOT_SENT", "ACCEPTED", "AMBIGUOUS"}


def test_runtime_intent_semantic_status_has_no_writer_outside_the_unique_reducer() -> None:
    root = Path(__file__).parents[2] / "src" / "app"
    forbidden: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative in {
            "runtime/orchestration/effect_state_contract.py",
            "runtime/orchestration/services/effect_reducer_service.py",
        }:
            continue
        source = path.read_text(encoding="utf-8")
        if "transition_runtime_intent(" in source or ".effect_status =" in source:
            forbidden.append(relative)
    assert forbidden == []


@pytest.mark.parametrize(
    ("outcome_kind", "expected_event"),
    [
        ("success", EffectReducerEventType.CALLBACK_COMPLETED),
        ("business_reject", EffectReducerEventType.CALLBACK_REJECTED),
        ("retryable_failure", EffectReducerEventType.TRANSPORT_NOT_SENT),
        ("contract_violation", EffectReducerEventType.TRANSPORT_NOT_SENT),
    ],
)
@pytest.mark.asyncio
async def test_local_transactional_outcome_also_uses_the_unique_reducer(
    outcome_kind: str,
    expected_event: EffectReducerEventType,
) -> None:
    _require_t8d()
    reducer = _RecordingReducer()
    service = SystemCapabilityIntentService(
        definitions={},
        plugin_definitions={},
        plugin_index_digest="d" * 64,
        effect_repository=object(),
        effect_reducer=reducer,
    )
    evidence_dict = {
        "outcome_kind": outcome_kind,
        "outcome_code": "OUTCOME",
        "occurred_at_ms": 1700,
    }
    evidence = SimpleNamespace(
        **evidence_dict,
        model_dump=lambda **_kwargs: dict(evidence_dict),
    )

    await service.record_outcome(
        {"db": _Db()},
        prepared=SimpleNamespace(claim={"dispatch_key": "dispatch-1"}),
        evidence=evidence,
    )

    assert [event.event_type for event in reducer.events] == [expected_event]
    if expected_event is EffectReducerEventType.TRANSPORT_NOT_SENT:
        assert reducer.events[0].attempt_no == 1
        assert reducer.events[0].retry_exhausted is True


@pytest.mark.asyncio
async def test_workline_transport_finalization_records_reducer_event_before_commit() -> None:
    _require_t8d()
    from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import OutboxDispatchService

    bridge = SimpleNamespace(record_result=AsyncMock())
    service = OutboxDispatchService(effect_transport_bridge=bridge)
    outbox = SimpleNamespace(dispatch_key="dispatch-1")
    updated = SimpleNamespace(status="SENT")
    outbox_repository = SimpleNamespace(mark_as_sent=AsyncMock(return_value=updated))
    attempt = SimpleNamespace(attempt_no=3)
    attempt_service = SimpleNamespace(finalize_external_http_attempt_record=AsyncMock(return_value=attempt))
    result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )

    finalized = await service._finalize_external_http_result(
        _Db(),
        outbox_repo=outbox_repository,
        outbox=outbox,
        outbox_id=1,
        dispatch_attempt=attempt,
        attempt_service=attempt_service,
        result=result,
    )

    assert finalized is updated
    bridge.record_result.assert_awaited_once()
    call = bridge.record_result.await_args
    assert call.kwargs["dispatch_key"] == "dispatch-1"
    assert call.kwargs["attempt_no"] == 3
    assert call.kwargs["result"] is result
    assert call.kwargs["retry_exhausted"] is False
