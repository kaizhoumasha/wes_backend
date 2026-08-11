"""E03/E07 同步义务的 typed reconciliation resolution 合同。"""

from __future__ import annotations

import importlib
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.app.runtime.orchestration.effect_bridges import EffectReconciliationBridge
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.models.operation import ResolveEffectReconciliationRequest
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reconciliation_resolution_service import (
    EffectReconciliationResolutionService,
)
from src.app.runtime.orchestration.services.effect_reducer_service import (
    EffectReducer,
    InvalidReconciliationEvent,
    ReconciliationResolutionConflict,
)
from src.app.runtime.orchestration.services.hold.wms_putaway_sync_barrier_service import (
    WmsPutawaySyncBarrierGroup,
    WmsPutawaySyncBarrierService,
)

E03 = "wms.inventory.confirm_inbound@v1"
E07 = "wms.fulfillment.notify_pkg_binding@v1"


def _typed_resolution(
    operation_identity: str = E03,
    *,
    fact_version: str = "material-fact:v3",
    source_event_id: str = "manual-obligation-resolution-1",
    evidence_reference: str = "wms-audit:E03:document-17",
) -> dict[str, str]:
    return {
        "resolved_operation_identity": operation_identity,
        "resolved_fact_version": fact_version,
        "resolution": "OBLIGATION_SATISFIED",
        "source_event_id": source_event_id,
        "evidence_reference": evidence_reference,
    }


def _request(operation_identity: str = E03) -> ResolveEffectReconciliationRequest:
    return ResolveEffectReconciliationRequest(
        obligation_resolution=_typed_resolution(operation_identity),
        operator_note="已核验 WMS 权威记录",
    )


class _Db:
    def __init__(self) -> None:
        self.flush = AsyncMock()
        self.commit = AsyncMock()


class _RuntimeHoldRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_open_hold(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(id=101)


class _Repository:
    def __init__(self, operation_identity: str = E03) -> None:
        capability_key, contract_version = operation_identity.rsplit("@", 1)
        self.intent = SimpleNamespace(
            id=17,
            dispatch_key="dispatch-1",
            capability_key=capability_key,
            capability_contract_version=contract_version,
            fact_version="material-fact:v3",
            effect_status=RuntimeIntentStatus.RECONCILING,
            outcome_kind="technical_failure",
            outcome_code="READ_TIMEOUT",
            outcome_json={"preserved": True},
            outcome_history_json=[{"event_type": "TRANSPORT_AMBIGUOUS"}],
            effect_updated_at_ms=900,
        )
        self.case = ReconciliationCase(
            runtime_intent_log_id=17,
            dispatch_key="dispatch-1",
            status=ReconciliationCaseStatus.OPEN,
            reason_code="TRANSPORT_AMBIGUOUS",
            evidence_history_json=[],
            decision_json={},
            opened_at_ms=900,
        )
        self.cases = [self.case]

    async def get_intent_for_update(self, _db: Any, dispatch_key: str) -> Any | None:
        return self.intent if dispatch_key == self.intent.dispatch_key else None

    async def get_open_case_for_update(self, _db: Any, dispatch_key: str) -> ReconciliationCase | None:
        return next(
            (
                case
                for case in reversed(self.cases)
                if case.dispatch_key == dispatch_key and case.status is ReconciliationCaseStatus.OPEN
            ),
            None,
        )

    async def list_resolved_cases_for_update(self, _db: Any, dispatch_key: str) -> tuple[ReconciliationCase, ...]:
        return tuple(
            case
            for case in reversed(self.cases)
            if case.dispatch_key == dispatch_key and case.status is ReconciliationCaseStatus.RESOLVED
        )

    def add_case(self, _db: Any, case: ReconciliationCase) -> None:
        self.cases.append(case)


def _typed_event(resolution: dict[str, str]) -> EffectReducerEvent:
    return EffectReducerEvent(
        event_type=EffectReducerEventType.RECONCILIATION_RESOLVED,
        dispatch_key="dispatch-1",
        occurred_at_ms=1_000,
        source_event_id=resolution["source_event_id"],
        obligation_resolution=resolution,
        reason_code="MANUAL_EFFECT_RECONCILIATION_RESOLUTION",
        evidence_json={"operator_id": 88, "operator_note": "已核验 WMS 权威记录"},
    )


@pytest.mark.asyncio
async def test_putaway_sync_hold_does_not_write_retired_plugin_identity() -> None:
    hold_repository = _RuntimeHoldRepository()
    service = WmsPutawaySyncBarrierService(runtime_hold_repository=hold_repository)  # type: ignore[arg-type]

    await service.create_hold(
        object(),
        group=WmsPutawaySyncBarrierGroup(
            execution_work_item_id=17,
            correlation_id="corr-17",
            fact_version="material-fact:v3",
        ),
        workline_id=7,
        session_id=11,
        trace_id="trace-11",
    )

    assert {"plugin_key", "contract_version"}.isdisjoint(hold_repository.calls[0])


def test_effect_resolution_request_accepts_only_complete_typed_obligation_resolution() -> None:
    request = _request()

    assert request.request_id is None
    assert request.resolution is None
    assert request.obligation_resolution.model_dump() == _typed_resolution()

    for missing_field in _typed_resolution():
        invalid = _typed_resolution()
        invalid.pop(missing_field)
        with pytest.raises(ValidationError):
            ResolveEffectReconciliationRequest(
                obligation_resolution=invalid,
                operator_note="已核验 WMS 权威记录",
            )

    with pytest.raises(ValidationError):
        ResolveEffectReconciliationRequest(
            obligation_resolution={**_typed_resolution(), "unexpected": "forbidden"},
            operator_note="已核验 WMS 权威记录",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_identity", [E03, E07])
async def test_typed_obligation_resolution_closes_case_without_mutating_intent(
    operation_identity: str,
) -> None:
    repository = _Repository(operation_identity)
    original_intent = deepcopy(vars(repository.intent))
    resolution = _typed_resolution(operation_identity)

    result = await EffectReducer(repository=repository).reduce(_Db(), _typed_event(resolution))

    assert result is not None
    assert result.intent_status is RuntimeIntentStatus.RECONCILING
    assert result.case_status is ReconciliationCaseStatus.RESOLVED
    assert result.state_changed is False
    assert vars(repository.intent) == original_intent
    assert repository.case.decision_json == resolution
    assert repository.case.resolved_at_ms == 1_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolution",
    [
        _typed_resolution(E07),
        _typed_resolution(E03, fact_version="material-fact:v999"),
    ],
)
async def test_typed_obligation_resolution_rejects_identity_or_fact_version_mismatch(
    resolution: dict[str, str],
) -> None:
    repository = _Repository(E03)
    original_intent = deepcopy(vars(repository.intent))

    with pytest.raises(InvalidReconciliationEvent):
        await EffectReducer(repository=repository).reduce(_Db(), _typed_event(resolution))

    assert repository.case.status is ReconciliationCaseStatus.OPEN
    assert repository.case.decision_json == {}
    assert vars(repository.intent) == original_intent


@pytest.mark.asyncio
async def test_typed_obligation_resolution_is_idempotent_and_rejects_same_source_drift() -> None:
    repository = _Repository(E03)
    reducer = EffectReducer(repository=repository)
    resolution = _typed_resolution()

    first = await reducer.reduce(_Db(), _typed_event(resolution))
    replay = await reducer.reduce(_Db(), _typed_event(resolution))

    assert first is not None and first.case_status is ReconciliationCaseStatus.RESOLVED
    assert replay is not None and replay.state_changed is False
    assert len(repository.cases) == 1

    drifted = _typed_resolution(evidence_reference="wms-audit:E03:different-document")
    with pytest.raises(ReconciliationResolutionConflict):
        await reducer.reduce(_Db(), _typed_event(drifted))


@pytest.mark.asyncio
async def test_non_obligation_effect_keeps_generic_resolution_semantics() -> None:
    repository = _Repository("wms.inventory.reserve_inventory@v1")
    result = await EffectReducer(repository=repository).reduce(
        _Db(),
        EffectReducerEvent(
            event_type=EffectReducerEventType.RECONCILIATION_RESOLVED,
            dispatch_key="dispatch-1",
            occurred_at_ms=1_000,
            source_event_id="generic-resolution-1",
            resolution=RuntimeIntentStatus.COMPLETED,
            reason_code="MANUAL_EFFECT_RECONCILIATION_RESOLUTION",
            evidence_json={"operator_id": 88},
        ),
    )

    assert result is not None
    assert result.intent_status is RuntimeIntentStatus.COMPLETED
    assert result.state_changed is True
    assert repository.intent.outcome_history_json[-1]["source_event_id"] == "generic-resolution-1"


@pytest.mark.asyncio
async def test_resolution_service_and_bridge_preserve_typed_obligation_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    bridge = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                intent_status=RuntimeIntentStatus.RECONCILING,
                case_status=ReconciliationCaseStatus.RESOLVED,
                state_changed=False,
            )
        )
    )
    db = _Db()
    service = EffectReconciliationResolutionService(
        reconciliation_bridge=bridge,
        outbox_repository=SimpleNamespace(
            get_by_dispatch_key=AsyncMock(return_value=SimpleNamespace(workline_id=7, operation_identity=E03))
        ),
        owner_scope_repository=SimpleNamespace(workline_is_owned_by=AsyncMock(return_value=True)),
    )

    response = await service.resolve(
        db,
        dispatch_key="dispatch-1",
        request_id=request.request_id,
        resolution=request.resolution,
        obligation_resolution=request.obligation_resolution,
        operator_note=request.operator_note,
        operator_id=88,
        is_superuser=False,
    )

    assert bridge.resolve.await_args.kwargs["obligation_resolution"] is request.obligation_resolution
    assert bridge.resolve.await_args.kwargs["source_event_id"] == "manual-obligation-resolution-1"
    assert response["resolution"] == "OBLIGATION_SATISFIED"
    assert response["intent_status"] == "RECONCILING"
    assert response["state_changed"] is False
    db.commit.assert_awaited_once()

    barrier_group = object()
    barrier_service = SimpleNamespace(
        lock_group_for_dispatch=AsyncMock(return_value=barrier_group),
        evaluate_dispatch=AsyncMock(return_value=None),
    )
    barrier_module = importlib.import_module(
        "src.app.runtime.orchestration.services.hold.wms_putaway_sync_barrier_service"
    )
    monkeypatch.setattr(barrier_module, "wms_putaway_sync_barrier_service", barrier_service)
    reducer = SimpleNamespace(reduce=AsyncMock(return_value="reduced"))
    result = await EffectReconciliationBridge(reducer=reducer).resolve(
        db,
        dispatch_key="dispatch-1",
        occurred_at_ms=1_000,
        resolution=None,
        obligation_resolution=request.obligation_resolution,
        reason_code="MANUAL_EFFECT_RECONCILIATION_RESOLUTION",
        evidence_json={"operator_id": 88},
        source_event_id="manual-obligation-resolution-1",
    )

    event = reducer.reduce.await_args.args[1]
    assert result == "reduced"
    assert event.obligation_resolution is request.obligation_resolution
    assert event.resolution is None
    barrier_service.lock_group_for_dispatch.assert_awaited_once_with(db, dispatch_key="dispatch-1")
    barrier_service.evaluate_dispatch.assert_awaited_once_with(
        db,
        dispatch_key="dispatch-1",
        locked_group=barrier_group,
    )
