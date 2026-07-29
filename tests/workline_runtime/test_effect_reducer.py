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
    from src.app.runtime.orchestration.services.effect_reconciliation_resolution_service import (
        EffectReconciliationResolutionService,
    )
    from src.app.runtime.orchestration.services.effect_reducer_service import (
        EffectIntentNotFound,
        EffectReducer,
        InvalidReconciliationEvent,
        ReconciliationEvidenceConflict,
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

    async def list_resolved_cases_for_update(self, _db: Any, dispatch_key: str) -> tuple[Any, ...]:
        return tuple(
            case
            for case in reversed(self.cases)
            if case.dispatch_key == dispatch_key and case.status is ReconciliationCaseStatus.RESOLVED
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
        EffectReducerEventType.TRANSPORT_REJECTED,
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
        (RuntimeIntentStatus.UNKNOWN, EffectReducerEventType.TRANSPORT_ACCEPTED, False, RuntimeIntentStatus.ACCEPTED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.TRANSPORT_REJECTED, False, RuntimeIntentStatus.REJECTED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.TRANSPORT_AMBIGUOUS, False, RuntimeIntentStatus.UNKNOWN),
        (RuntimeIntentStatus.ACCEPTED, EffectReducerEventType.TRANSPORT_AMBIGUOUS, False, RuntimeIntentStatus.UNKNOWN),
        (
            RuntimeIntentStatus.PROPOSED,
            EffectReducerEventType.LOCAL_REDECISION_REQUIRED,
            False,
            RuntimeIntentStatus.PROPOSED,
        ),
        (
            RuntimeIntentStatus.PROPOSED,
            EffectReducerEventType.DISPATCH_CANCELLED,
            False,
            RuntimeIntentStatus.RECONCILING,
        ),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.CALLBACK_ACCEPTED, False, RuntimeIntentStatus.ACCEPTED),
        (RuntimeIntentStatus.UNKNOWN, EffectReducerEventType.CALLBACK_ACCEPTED, False, RuntimeIntentStatus.ACCEPTED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.CALLBACK_COMPLETED, False, RuntimeIntentStatus.COMPLETED),
        (RuntimeIntentStatus.ACCEPTED, EffectReducerEventType.CALLBACK_COMPLETED, False, RuntimeIntentStatus.COMPLETED),
        (RuntimeIntentStatus.UNKNOWN, EffectReducerEventType.CALLBACK_COMPLETED, False, RuntimeIntentStatus.COMPLETED),
        (RuntimeIntentStatus.PROPOSED, EffectReducerEventType.CALLBACK_REJECTED, False, RuntimeIntentStatus.REJECTED),
        (RuntimeIntentStatus.ACCEPTED, EffectReducerEventType.CALLBACK_REJECTED, False, RuntimeIntentStatus.REJECTED),
        (RuntimeIntentStatus.UNKNOWN, EffectReducerEventType.CALLBACK_REJECTED, False, RuntimeIntentStatus.REJECTED),
        (
            RuntimeIntentStatus.PROPOSED,
            EffectReducerEventType.RECONCILIATION_OPENED,
            False,
            RuntimeIntentStatus.RECONCILING,
        ),
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
async def test_recovered_transport_ack_from_unknown_writes_existing_authoritative_envelope() -> None:
    repository = _ReducerRepository(RuntimeIntentStatus.UNKNOWN)
    repository.intent.capability_key = "wms.fulfillment.request_rack_supply"
    repository.intent.capability_contract_version = "v1"
    repository.intent.operation_identity = "WMS:PKG-001"
    repository.intent.idempotency_key = "idem-001"
    repository.intent.payload_hash = "a" * 64
    ack_outcome = {
        "kind": "success",
        "payload": {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "idem-001",
            "provider_reference": "provider-status-first",
            "submission_state": "REPLAY",
            "accepted_scope": None,
        },
    }
    event = _event(EffectReducerEventType.TRANSPORT_ACCEPTED).model_copy(
        update={
            "evidence_json": {
                "typed_ack_hash": "b" * 64,
                "typed_ack_reference": "runtime-intent-outcome:dispatch-1",
            },
            "terminal_outcome": ack_outcome,
        }
    )

    await EffectReducer(repository=repository).reduce(_Db(), event)

    assert repository.intent.effect_status is RuntimeIntentStatus.ACCEPTED
    assert repository.intent.outcome_json["outcome"] == ack_outcome
    assert "payload" not in repository.intent.outcome_history_json[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type_value", "expected"),
    [
        ("STATUS_ACCEPTED", RuntimeIntentStatus.ACCEPTED),
        ("STATUS_PROCESSING", RuntimeIntentStatus.ACCEPTED),
        ("STATUS_COMPLETED", RuntimeIntentStatus.COMPLETED),
        ("STATUS_REJECTED", RuntimeIntentStatus.REJECTED),
    ],
)
async def test_status_snapshot_events_are_the_authoritative_wms_semantic_progression(
    event_type_value: str,
    expected: RuntimeIntentStatus,
) -> None:
    repository = _ReducerRepository()
    event_type = EffectReducerEventType(event_type_value)
    event = _event(event_type).model_copy(
        update={
            "source_event_id": f"wms-status:{event_type.value}:7",
            "evidence_json": {"source_version": 7, "snapshot_hash": "a" * 64},
        }
    )

    await EffectReducer(repository=repository).reduce(_Db(), event)
    replay = await EffectReducer(repository=repository).reduce(_Db(), event)

    assert repository.intent.effect_status is expected
    assert replay is not None and replay.state_changed is False
    assert len(repository.intent.outcome_history_json) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        EffectReducerEventType.CALLBACK_COMPLETED,
        EffectReducerEventType.CALLBACK_REJECTED,
    ],
)
async def test_wms_status_enabled_intent_records_callback_without_advancing_terminal(
    event_type: EffectReducerEventType,
) -> None:
    repository = _ReducerRepository(RuntimeIntentStatus.ACCEPTED)
    repository.intent.capability_key = "wms.fulfillment.request_rack_supply"
    repository.intent.capability_contract_version = "v1"
    repository.intent.operation_identity = "STATION-001:FLOW_RACK:1"
    # callback authority 由 operation/status capability 身份决定，不依赖可损坏的 binding。
    repository.intent.status_binding_snapshot_hash = None
    repository.intent.status_binding_snapshot_json = None

    result = await EffectReducer(repository=repository).reduce(_Db(), _event(event_type))

    assert result is not None and result.state_changed is False
    assert repository.intent.effect_status is RuntimeIntentStatus.ACCEPTED
    assert repository.intent.outcome_history_json[0]["event_type"] == event_type.value


@pytest.mark.asyncio
async def test_non_wms_intent_with_incidental_binding_hash_keeps_callback_authority() -> None:
    repository = _ReducerRepository(RuntimeIntentStatus.ACCEPTED)
    repository.intent.capability_key = "runtime.session_hold"
    repository.intent.capability_contract_version = "v1"
    repository.intent.operation_identity = "hold-instance-001"
    repository.intent.status_binding_snapshot_hash = "a" * 64

    result = await EffectReducer(repository=repository).reduce(
        _Db(),
        _event(EffectReducerEventType.CALLBACK_COMPLETED),
    )

    assert result is not None and result.state_changed is True
    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED


@pytest.mark.asyncio
async def test_conflicting_status_terminals_keep_first_terminal_and_open_reconciliation() -> None:
    repository = _ReducerRepository()
    reducer = EffectReducer(repository=repository)
    status_completed = EffectReducerEventType("STATUS_COMPLETED")
    status_rejected = EffectReducerEventType("STATUS_REJECTED")

    await reducer.reduce(
        _Db(),
        _event(status_completed).model_copy(update={"evidence_json": {"source_version": 7, "snapshot_hash": "a" * 64}}),
    )
    conflict = await reducer.reduce(
        _Db(),
        _event(status_rejected, occurred_at_ms=1200).model_copy(
            update={
                "reason_code": "WMS_BUSINESS_REJECTED",
                "evidence_json": {"source_version": 8, "snapshot_hash": "b" * 64},
            }
        ),
    )

    assert conflict is not None and conflict.contradiction is True
    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN


@pytest.mark.asyncio
async def test_callback_completed_before_transport_rejected_opens_contradiction_case() -> None:
    repository = _ReducerRepository()
    reducer = EffectReducer(repository=repository)

    await reducer.reduce(_Db(), _event(EffectReducerEventType.CALLBACK_COMPLETED))
    contradiction = await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.TRANSPORT_REJECTED, occurred_at_ms=1001),
    )

    assert contradiction is not None and contradiction.contradiction is True
    assert contradiction.case_created is True
    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN


@pytest.mark.asyncio
async def test_callback_rejected_before_transport_accepted_is_not_a_contradiction() -> None:
    repository = _ReducerRepository()
    reducer = EffectReducer(repository=repository)

    await reducer.reduce(_Db(), _event(EffectReducerEventType.CALLBACK_REJECTED))
    transport = await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.TRANSPORT_ACCEPTED, occurred_at_ms=1001),
    )

    assert transport is not None and transport.contradiction is False
    assert transport.case_created is False
    assert repository.intent.effect_status is RuntimeIntentStatus.REJECTED
    assert repository.cases == []


@pytest.mark.asyncio
async def test_dispatch_cancellation_enters_reconciling_and_opens_case() -> None:
    repository = _ReducerRepository()

    result = await EffectReducer(repository=repository).reduce(
        _Db(),
        _event(EffectReducerEventType.DISPATCH_CANCELLED, reason_code="OUTBOX_CANCELLED"),
    )

    assert result is not None
    assert result.intent_status is RuntimeIntentStatus.RECONCILING
    assert result.case_created is True
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN


@pytest.mark.asyncio
async def test_transport_rejected_before_callback_completed_opens_same_contradiction_case() -> None:
    repository = _ReducerRepository()
    reducer = EffectReducer(repository=repository)

    await reducer.reduce(_Db(), _event(EffectReducerEventType.TRANSPORT_REJECTED))
    contradiction = await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.CALLBACK_COMPLETED, occurred_at_ms=1001),
    )

    assert contradiction is not None and contradiction.contradiction is True
    assert contradiction.case_created is True
    assert repository.intent.effect_status is RuntimeIntentStatus.REJECTED
    assert repository.cases[0].status is ReconciliationCaseStatus.OPEN


@pytest.mark.asyncio
async def test_resolution_without_source_event_id_does_not_match_historical_resolution() -> None:
    _require_t8d()
    repository = _ReducerRepository(RuntimeIntentStatus.RECONCILING)
    historical_case = ReconciliationCase(
        runtime_intent_log_id=17,
        dispatch_key="dispatch-1",
        status=ReconciliationCaseStatus.RESOLVED,
        reason_code="OLD_CASE",
        evidence_history_json=[],
        decision_json={"source_event_id": None, "resolution": RuntimeIntentStatus.COMPLETED.value},
        opened_at_ms=800,
        resolved_at_ms=900,
    )
    open_case = ReconciliationCase(
        runtime_intent_log_id=17,
        dispatch_key="dispatch-1",
        status=ReconciliationCaseStatus.OPEN,
        reason_code="NEW_CASE",
        evidence_history_json=[],
        decision_json={},
        opened_at_ms=1_000,
    )
    repository.cases.extend([historical_case, open_case])
    event = _event(
        EffectReducerEventType.RECONCILIATION_RESOLVED,
        occurred_at_ms=1_100,
        resolution=RuntimeIntentStatus.COMPLETED,
    )
    event = event.model_copy(update={"source_event_id": None})

    result = await EffectReducer(repository=repository).reduce(_Db(), event)

    assert result is not None
    assert open_case.status is ReconciliationCaseStatus.RESOLVED
    assert repository.intent.effect_status is RuntimeIntentStatus.COMPLETED


@pytest.mark.asyncio
async def test_resolution_request_id_cannot_be_reused_with_a_different_decision() -> None:
    _require_t8d()
    repository = _ReducerRepository(RuntimeIntentStatus.RECONCILING)
    historical_case = ReconciliationCase(
        runtime_intent_log_id=17,
        dispatch_key="dispatch-1",
        status=ReconciliationCaseStatus.RESOLVED,
        reason_code="OLD_CASE",
        evidence_history_json=[],
        decision_json={
            "source_event_id": "manual-resolution-1",
            "resolution": RuntimeIntentStatus.COMPLETED.value,
        },
        opened_at_ms=800,
        resolved_at_ms=900,
    )
    open_case = ReconciliationCase(
        runtime_intent_log_id=17,
        dispatch_key="dispatch-1",
        status=ReconciliationCaseStatus.OPEN,
        reason_code="NEW_CASE",
        evidence_history_json=[],
        decision_json={},
        opened_at_ms=1_000,
    )
    repository.cases.extend([historical_case, open_case])
    event = _event(
        EffectReducerEventType.RECONCILIATION_RESOLVED,
        occurred_at_ms=1_100,
        resolution=RuntimeIntentStatus.REJECTED,
    ).model_copy(update={"source_event_id": "manual-resolution-1"})

    with pytest.raises(InvalidReconciliationEvent, match="different resolution"):
        await EffectReducer(repository=repository).reduce(_Db(), event)

    assert open_case.status is ReconciliationCaseStatus.OPEN
    assert repository.intent.effect_status is RuntimeIntentStatus.RECONCILING


@pytest.mark.asyncio
async def test_effect_reconciliation_resolution_service_commits_stable_operator_decision() -> None:
    _require_t8d()
    outbox_repository = SimpleNamespace(
        get_by_dispatch_key=AsyncMock(return_value=SimpleNamespace(workline_id=7)),
    )
    owner_scope_repository = SimpleNamespace(workline_is_owned_by=AsyncMock(return_value=True))
    bridge = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                intent_status=RuntimeIntentStatus.COMPLETED,
                case_status=ReconciliationCaseStatus.RESOLVED,
                state_changed=True,
            )
        )
    )
    db = SimpleNamespace(commit=AsyncMock())

    result = await EffectReconciliationResolutionService(
        reconciliation_bridge=bridge,
        outbox_repository=outbox_repository,
        owner_scope_repository=owner_scope_repository,
    ).resolve(
        db,
        dispatch_key="dispatch-1",
        request_id=" resolution-request-1 ",
        resolution="COMPLETED",
        operator_note="已核验 WMS 权威记录",
        operator_id=88,
        is_superuser=False,
    )

    outbox_repository.get_by_dispatch_key.assert_awaited_once_with(db, "dispatch-1")
    owner_scope_repository.workline_is_owned_by.assert_awaited_once_with(
        db,
        workline_id=7,
        tenant_id=88,
    )
    bridge.resolve.assert_awaited_once()
    assert bridge.resolve.await_args.kwargs["source_event_id"] == "resolution-request-1"
    assert bridge.resolve.await_args.kwargs["evidence_json"] == {
        "operator_id": 88,
        "operator_note": "已核验 WMS 权威记录",
        "request_id": "resolution-request-1",
    }
    db.commit.assert_awaited_once()
    assert result == {
        "dispatch_key": "dispatch-1",
        "resolution": "COMPLETED",
        "request_id": "resolution-request-1",
        "intent_status": "COMPLETED",
        "case_status": "RESOLVED",
        "state_changed": True,
    }


@pytest.mark.asyncio
async def test_effect_reconciliation_resolution_rejects_cross_workline_operator_before_reducer() -> None:
    _require_t8d()
    outbox_repository = SimpleNamespace(
        get_by_dispatch_key=AsyncMock(return_value=SimpleNamespace(workline_id=7)),
    )
    owner_scope_repository = SimpleNamespace(workline_is_owned_by=AsyncMock(return_value=False))
    bridge = SimpleNamespace(resolve=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())

    from src.core.exceptions import PermissionException

    with pytest.raises(PermissionException, match="无权提交该 WorkLine"):
        await EffectReconciliationResolutionService(
            reconciliation_bridge=bridge,
            outbox_repository=outbox_repository,
            owner_scope_repository=owner_scope_repository,
        ).resolve(
            db,
            dispatch_key="dispatch-other-owner",
            request_id="resolution-request-2",
            resolution="REJECTED",
            operator_note="跨 WorkLine 请求",
            operator_id=88,
            is_superuser=False,
        )

    bridge.resolve.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_effect_reconciliation_resolution_allows_superuser_without_owner_lookup() -> None:
    _require_t8d()
    outbox_repository = SimpleNamespace(
        get_by_dispatch_key=AsyncMock(return_value=SimpleNamespace(workline_id=7)),
    )
    owner_scope_repository = SimpleNamespace(workline_is_owned_by=AsyncMock())
    bridge = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                intent_status=RuntimeIntentStatus.REJECTED,
                case_status=ReconciliationCaseStatus.RESOLVED,
                state_changed=True,
            )
        )
    )
    db = SimpleNamespace(commit=AsyncMock())

    await EffectReconciliationResolutionService(
        reconciliation_bridge=bridge,
        outbox_repository=outbox_repository,
        owner_scope_repository=owner_scope_repository,
    ).resolve(
        db,
        dispatch_key="dispatch-1",
        request_id="resolution-request-3",
        resolution="REJECTED",
        operator_note="平台管理员决议",
        operator_id=1,
        is_superuser=True,
    )

    owner_scope_repository.workline_is_owned_by.assert_not_awaited()
    bridge.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_effect_reconciliation_bridge_rejects_blank_resolution_identity() -> None:
    _require_t8d()
    reducer = SimpleNamespace(reduce=AsyncMock())

    with pytest.raises(ValueError, match="stable source_event_id"):
        await EffectReconciliationBridge(reducer=reducer).resolve(
            SimpleNamespace(),
            dispatch_key="dispatch-1",
            occurred_at_ms=1_000,
            resolution=RuntimeIntentStatus.COMPLETED,
            reason_code="MANUAL",
            evidence_json={},
            source_event_id=" ",
        )

    reducer.reduce.assert_not_awaited()


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
async def test_resolved_contradictory_callback_replay_does_not_reopen_case() -> None:
    repository = _ReducerRepository(RuntimeIntentStatus.COMPLETED)
    reducer = EffectReducer(repository=repository)
    callback = _event(EffectReducerEventType.CALLBACK_REJECTED, occurred_at_ms=1000).model_copy(
        update={"source_event_id": "callback-rejected-1"}
    )

    await reducer.reduce(_Db(), callback)
    await reducer.reduce(
        _Db(),
        _event(
            EffectReducerEventType.RECONCILIATION_RESOLVED,
            occurred_at_ms=1100,
            resolution=RuntimeIntentStatus.COMPLETED,
            reason_code="KEEP_COMPLETED",
        ),
    )
    case_history_size = len(repository.cases[0].evidence_history_json)
    intent_history_size = len(repository.intent.outcome_history_json)

    replay = await reducer.reduce(_Db(), callback.model_copy(update={"occurred_at_ms": 1200}))

    assert replay is not None and replay.state_changed is False
    assert replay.case_status is ReconciliationCaseStatus.RESOLVED
    assert len(repository.cases) == 1
    assert len(repository.cases[0].evidence_history_json) == case_history_size
    assert len(repository.intent.outcome_history_json) == intent_history_size


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
async def test_open_case_transport_evidence_replay_is_idempotent_and_conflict_is_rejected() -> None:
    repository = _ReducerRepository(RuntimeIntentStatus.ACCEPTED)
    reducer = EffectReducer(repository=repository)
    await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.RECONCILIATION_OPENED, reason_code="TRANSPORT_AMBIGUOUS"),
    )
    transport = _event(EffectReducerEventType.TRANSPORT_AMBIGUOUS, occurred_at_ms=1100).model_copy(
        update={"source_event_id": "transport:stable", "evidence_json": {"fact": "READ_TIMEOUT"}}
    )

    await reducer.reduce(_Db(), transport)
    case_history_size = len(repository.cases[0].evidence_history_json)
    intent_history_size = len(repository.intent.outcome_history_json)
    replay = await reducer.reduce(_Db(), transport.model_copy(update={"occurred_at_ms": 1200}))

    assert replay is not None and replay.state_changed is False
    assert len(repository.cases[0].evidence_history_json) == case_history_size
    assert len(repository.intent.outcome_history_json) == intent_history_size

    conflicting = transport.model_copy(update={"evidence_json": {"fact": "LEASE_EXPIRED"}})
    with pytest.raises(ReconciliationEvidenceConflict):
        await reducer.reduce(_Db(), conflicting)


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
async def test_idempotency_conflict_enters_reconciling_before_resolution() -> None:
    repository = _ReducerRepository(RuntimeIntentStatus.PROPOSED)
    reducer = EffectReducer(repository=repository)

    opened = await reducer.reduce(_Db(), _event(EffectReducerEventType.IDEMPOTENCY_CONFLICT))
    resolved = await reducer.reduce(
        _Db(),
        _event(
            EffectReducerEventType.RECONCILIATION_RESOLVED,
            occurred_at_ms=1200,
            resolution=RuntimeIntentStatus.REJECTED,
        ),
    )

    assert opened is not None and opened.intent_status is RuntimeIntentStatus.RECONCILING
    assert resolved is not None and resolved.intent_status is RuntimeIntentStatus.REJECTED
    assert repository.cases[0].status is ReconciliationCaseStatus.RESOLVED


@pytest.mark.asyncio
async def test_resolved_idempotency_conflict_opening_event_replay_does_not_create_new_case() -> None:
    repository = _ReducerRepository(RuntimeIntentStatus.PROPOSED)
    reducer = EffectReducer(repository=repository)
    conflict = _event(EffectReducerEventType.IDEMPOTENCY_CONFLICT)

    await reducer.reduce(_Db(), conflict)
    await reducer.reduce(
        _Db(),
        _event(
            EffectReducerEventType.RECONCILIATION_RESOLVED,
            occurred_at_ms=1200,
            resolution=RuntimeIntentStatus.REJECTED,
        ),
    )
    replay = await reducer.reduce(_Db(), conflict)

    assert replay is not None
    assert replay.intent_status is RuntimeIntentStatus.REJECTED
    assert replay.case_status is ReconciliationCaseStatus.RESOLVED
    assert replay.case_created is False
    assert len(repository.cases) == 1


@pytest.mark.asyncio
async def test_reconciliation_resolution_replay_returns_existing_resolution() -> None:
    _require_t8d()
    repository = _ReducerRepository(RuntimeIntentStatus.ACCEPTED)
    reducer = EffectReducer(repository=repository)
    await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.RECONCILIATION_OPENED, reason_code="TRANSPORT_AMBIGUOUS"),
    )
    resolution = _event(
        EffectReducerEventType.RECONCILIATION_RESOLVED,
        occurred_at_ms=1200,
        resolution=RuntimeIntentStatus.COMPLETED,
        reason_code="REMOTE_CONFIRMED",
    )

    first = await reducer.reduce(_Db(), resolution)
    history_size = len(repository.intent.outcome_history_json)
    replay = await reducer.reduce(_Db(), resolution)

    assert first is not None and replay is not None
    assert replay.intent_status is RuntimeIntentStatus.COMPLETED
    assert replay.case_status is ReconciliationCaseStatus.RESOLVED
    assert replay.state_changed is False
    assert len(repository.intent.outcome_history_json) == history_size


@pytest.mark.asyncio
async def test_old_reconciliation_resolution_replay_remains_idempotent_after_newer_case() -> None:
    _require_t8d()
    repository = _ReducerRepository(RuntimeIntentStatus.ACCEPTED)
    reducer = EffectReducer(repository=repository)
    await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.RECONCILIATION_OPENED, occurred_at_ms=1100, reason_code="CASE_A"),
    )
    resolution_a = _event(
        EffectReducerEventType.RECONCILIATION_RESOLVED,
        occurred_at_ms=1200,
        resolution=RuntimeIntentStatus.COMPLETED,
        reason_code="REMOTE_CONFIRMED_A",
    )
    await reducer.reduce(_Db(), resolution_a)
    await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.RECONCILIATION_OPENED, occurred_at_ms=1300, reason_code="CASE_B"),
    )
    await reducer.reduce(
        _Db(),
        _event(
            EffectReducerEventType.RECONCILIATION_RESOLVED,
            occurred_at_ms=1400,
            resolution=RuntimeIntentStatus.COMPLETED,
            reason_code="REMOTE_CONFIRMED_B",
        ),
    )

    replay = await reducer.reduce(_Db(), resolution_a)

    assert replay is not None
    assert replay.intent_status is RuntimeIntentStatus.COMPLETED
    assert replay.case_status is ReconciliationCaseStatus.RESOLVED
    assert replay.state_changed is False


@pytest.mark.asyncio
async def test_old_resolution_replay_does_not_close_new_open_case() -> None:
    _require_t8d()
    repository = _ReducerRepository(RuntimeIntentStatus.ACCEPTED)
    reducer = EffectReducer(repository=repository)
    await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.RECONCILIATION_OPENED, occurred_at_ms=1100, reason_code="CASE_A"),
    )
    resolution_a = _event(
        EffectReducerEventType.RECONCILIATION_RESOLVED,
        occurred_at_ms=1200,
        resolution=RuntimeIntentStatus.COMPLETED,
        reason_code="REMOTE_CONFIRMED_A",
    )
    await reducer.reduce(_Db(), resolution_a)
    await reducer.reduce(
        _Db(),
        _event(EffectReducerEventType.RECONCILIATION_OPENED, occurred_at_ms=1300, reason_code="CASE_B"),
    )
    open_case_b = repository.cases[-1]

    replay = await reducer.reduce(_Db(), resolution_a)

    assert replay is not None
    assert replay.state_changed is False
    assert replay.case_status is ReconciliationCaseStatus.OPEN
    assert open_case_b.status is ReconciliationCaseStatus.OPEN
    assert open_case_b.decision_json == {}


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
            ExternalHttpTransportResult.accepted(
                http_status_code=409,
                protocol_result=ExternalHttpProtocolResult.REJECTED,
                error_code="HTTP_REJECTED",
            ),
            False,
            [EffectReducerEventType.TRANSPORT_REJECTED],
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
@pytest.mark.parametrize(
    ("status_code", "protocol_error_code", "expected_events"),
    [
        (
            409,
            "IDEMPOTENCY_REQUEST_IN_PROGRESS",
            [EffectReducerEventType.TRANSPORT_ACCEPTED],
        ),
        (
            422,
            "IDEMPOTENCY_CONFLICT",
            [
                EffectReducerEventType.TRANSPORT_ACCEPTED,
                EffectReducerEventType.IDEMPOTENCY_CONFLICT,
            ],
        ),
        (
            409,
            None,
            [
                EffectReducerEventType.TRANSPORT_ACCEPTED,
                EffectReducerEventType.RECONCILIATION_OPENED,
            ],
        ),
        (
            422,
            "IDEMPOTENCY_REQUEST_IN_PROGRESS",
            [
                EffectReducerEventType.TRANSPORT_ACCEPTED,
                EffectReducerEventType.RECONCILIATION_OPENED,
            ],
        ),
    ],
)
async def test_wms_effect_transport_bridge_interprets_only_stable_idempotency_protocol_combinations(
    status_code: int,
    protocol_error_code: str | None,
    expected_events: list[EffectReducerEventType],
) -> None:
    reducer = _RecordingReducer()
    result = ExternalHttpTransportResult.accepted(
        http_status_code=status_code,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        protocol_error_code=protocol_error_code,
        error_code="HTTP_REJECTED",
    )

    await EffectTransportBridge(reducer=reducer).record_result(
        _Db(),
        dispatch_key="dispatch-1",
        attempt_no=2,
        result=result,
        retry_exhausted=False,
        occurred_at_ms=1300,
        operation_identity="wms.fulfillment.notify_pkg_binding@v1",
    )

    assert [event.event_type for event in reducer.events] == expected_events


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 429])
async def test_wms_effect_non_idempotency_rejections_open_reconciliation(status_code: int) -> None:
    reducer = _RecordingReducer()
    result = ExternalHttpTransportResult.accepted(
        http_status_code=status_code,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        error_code="HTTP_REJECTED",
    )

    await EffectTransportBridge(reducer=reducer).record_result(
        _Db(),
        dispatch_key="dispatch-1",
        attempt_no=2,
        result=result,
        retry_exhausted=False,
        occurred_at_ms=1300,
        operation_identity="wms.fulfillment.notify_pkg_binding@v1",
    )

    assert [event.event_type for event in reducer.events] == [
        EffectReducerEventType.TRANSPORT_ACCEPTED,
        EffectReducerEventType.RECONCILIATION_OPENED,
    ]
    assert reducer.events[-1].reason_code == "WMS_SUBMIT_PROTOCOL_REJECTED"


@pytest.mark.asyncio
async def test_non_wms_409_remains_generic_transport_rejection() -> None:
    reducer = _RecordingReducer()

    await EffectTransportBridge(reducer=reducer).record_result(
        _Db(),
        dispatch_key="dispatch-generic",
        attempt_no=1,
        result=ExternalHttpTransportResult.accepted(
            http_status_code=409,
            protocol_result=ExternalHttpProtocolResult.REJECTED,
            protocol_error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
            error_code="HTTP_REJECTED",
        ),
        retry_exhausted=False,
        occurred_at_ms=1300,
        operation_identity="generic.webhook@v1",
    )

    assert [event.event_type for event in reducer.events] == [EffectReducerEventType.TRANSPORT_REJECTED]


@pytest.mark.asyncio
async def test_generated_transport_source_event_ids_are_bounded_for_max_dispatch_key() -> None:
    reducer = _RecordingReducer()
    dispatch_key = "d" * 240

    await EffectTransportBridge(reducer=reducer).record_result(
        _Db(),
        dispatch_key=dispatch_key,
        attempt_no=1,
        result=ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code="READ_TIMEOUT",
        ),
        retry_exhausted=False,
        occurred_at_ms=1300,
    )

    assert len(reducer.events) == 2
    assert all(event.source_event_id is not None and len(event.source_event_id) <= 240 for event in reducer.events)
    assert reducer.events[0].source_event_id != reducer.events[1].source_event_id


@pytest.mark.asyncio
async def test_transport_source_event_id_is_evidence_specific_and_replay_stable() -> None:
    first_reducer = _RecordingReducer()
    replay_reducer = _RecordingReducer()
    synthetic = ExternalHttpTransportResult.ambiguous(
        phase=ExternalHttpTransportPhase.SENDING,
        error_code="EXTERNAL_HTTP_LEASE_EXPIRED",
    )
    late_result = ExternalHttpTransportResult.ambiguous(
        phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
        error_code="READ_TIMEOUT",
    )
    bridge = EffectTransportBridge(reducer=first_reducer)

    await bridge.record_result(
        _Db(),
        dispatch_key="dispatch-1",
        attempt_no=2,
        result=synthetic,
        retry_exhausted=False,
        occurred_at_ms=1300,
    )
    await bridge.record_result(
        _Db(),
        dispatch_key="dispatch-1",
        attempt_no=2,
        result=late_result,
        retry_exhausted=False,
        occurred_at_ms=1400,
    )
    await EffectTransportBridge(reducer=replay_reducer).record_result(
        _Db(),
        dispatch_key="dispatch-1",
        attempt_no=2,
        result=late_result,
        retry_exhausted=False,
        occurred_at_ms=1500,
    )

    assert first_reducer.events[0].source_event_id != first_reducer.events[2].source_event_id
    assert first_reducer.events[2].source_event_id == replay_reducer.events[0].source_event_id
    assert first_reducer.events[3].source_event_id == replay_reducer.events[1].source_event_id


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
        source_event_id="resolution-1",
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
        ("business_reject", EffectReducerEventType.LOCAL_REDECISION_REQUIRED),
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
        assert reducer.events[0].retry_exhausted is (outcome_kind == "contract_violation")


@pytest.mark.asyncio
async def test_local_effect_generated_source_event_id_is_bounded_for_max_dispatch_key() -> None:
    reducer = _RecordingReducer()
    service = SystemCapabilityIntentService(
        definitions={},
        plugin_definitions={},
        plugin_index_digest="d" * 64,
        effect_repository=object(),
        effect_reducer=reducer,
    )
    evidence_dict = {
        "outcome_kind": "success",
        "outcome_code": "SUCCESS",
        "occurred_at_ms": 1700,
    }
    evidence = SimpleNamespace(
        **evidence_dict,
        model_dump=lambda **_kwargs: dict(evidence_dict),
    )

    await service.record_outcome(
        {"db": _Db()},
        prepared=SimpleNamespace(claim={"dispatch_key": "d" * 240}),
        evidence=evidence,
    )

    assert len(reducer.events) == 1
    assert reducer.events[0].source_event_id is not None
    assert len(reducer.events[0].source_event_id) <= 240


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
    db = _Db()

    finalized = await service._finalize_external_http_result(
        db,
        outbox_repo=outbox_repository,
        outbox=outbox,
        outbox_id=1,
        dispatch_attempt=attempt,
        attempt_service=attempt_service,
        result=result,
        lease_owner_token="worker-1",
        retry_budget=3,
    )

    assert finalized is updated
    outbox_repository.mark_as_sent.assert_awaited_once_with(db, 1, lease_owner_token="worker-1")
    bridge.record_result.assert_awaited_once()
    call = bridge.record_result.await_args
    assert call.kwargs["dispatch_key"] == "dispatch-1"
    assert call.kwargs["attempt_no"] == 3
    assert call.kwargs["result"] is result
    assert call.kwargs["retry_exhausted"] is False


@pytest.mark.asyncio
async def test_workline_protocol_rejection_finishes_sent_outbox_with_rejection_reason() -> None:
    from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import OutboxDispatchService

    bridge = SimpleNamespace(record_result=AsyncMock())
    service = OutboxDispatchService(effect_transport_bridge=bridge)
    outbox = SimpleNamespace(dispatch_key="dispatch-rejected")
    updated = SimpleNamespace(status="SENT", finished_at=object(), last_error="HTTP_REJECTED")
    outbox_repository = SimpleNamespace(
        mark_as_sent=AsyncMock(),
        mark_as_protocol_rejected=AsyncMock(return_value=updated),
    )
    attempt = SimpleNamespace(attempt_no=3)
    attempt_service = SimpleNamespace(finalize_external_http_attempt_record=AsyncMock(return_value=attempt))
    result = ExternalHttpTransportResult.accepted(
        http_status_code=409,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        error_code="HTTP_REJECTED",
    )
    db = _Db()

    finalized = await service._finalize_external_http_result(
        db,
        outbox_repo=outbox_repository,
        outbox=outbox,
        outbox_id=1,
        dispatch_attempt=attempt,
        attempt_service=attempt_service,
        result=result,
        lease_owner_token="worker-1",
        retry_budget=3,
    )

    assert finalized is updated
    outbox_repository.mark_as_protocol_rejected.assert_awaited_once_with(
        db,
        1,
        "HTTP_REJECTED",
        lease_owner_token="worker-1",
    )
    outbox_repository.mark_as_sent.assert_not_awaited()
    bridge.record_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_winning_transport_race_still_finalizes_current_attempt() -> None:
    _require_t8d()
    from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import OutboxDispatchService

    bridge = SimpleNamespace(record_result=AsyncMock())
    service = OutboxDispatchService(effect_transport_bridge=bridge)
    outbox = SimpleNamespace(dispatch_key="dispatch-race")
    callback_completed = SimpleNamespace(status="SENT", dispatch_key="dispatch-race")
    outbox_repository = SimpleNamespace(
        mark_as_sent=AsyncMock(return_value=None),
        get_by_id_for_update=AsyncMock(return_value=callback_completed),
    )
    attempt = SimpleNamespace(attempt_no=4)
    attempt_service = SimpleNamespace(finalize_external_http_attempt_record=AsyncMock(return_value=attempt))
    result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
    )
    db = _Db()

    finalized = await service._finalize_external_http_result(
        db,
        outbox_repo=outbox_repository,
        outbox=outbox,
        outbox_id=1,
        dispatch_attempt=attempt,
        attempt_service=attempt_service,
        result=result,
        lease_owner_token="worker-race",
        retry_budget=3,
    )

    assert finalized is callback_completed
    outbox_repository.get_by_id_for_update.assert_awaited_once_with(db, 1)
    attempt_service.finalize_external_http_attempt_record.assert_awaited_once()
    bridge.record_result.assert_awaited_once()
