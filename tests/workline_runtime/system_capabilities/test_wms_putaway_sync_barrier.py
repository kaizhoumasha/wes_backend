"""E03/E07 双义务同步屏障纯逻辑合同。"""

from __future__ import annotations

import importlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_bridges import (
    EffectReconciliationBridge,
    EffectTransportBridge,
    EffectTransportResolution,
)
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHoldStatus, RuntimeHoldType
from src.app.runtime.orchestration.services.hold.wms_putaway_sync_barrier_service import (
    WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES,
    WmsPutawaySyncBarrierGroup,
    WmsPutawaySyncBarrierService,
)
from src.app.runtime.orchestration.wms_sync_obligation import (
    WmsSyncObligationResolution,
)

E03, E07 = WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES
barrier_module = importlib.import_module("src.app.runtime.orchestration.services.hold.wms_putaway_sync_barrier_service")
GROUP = WmsPutawaySyncBarrierGroup(
    execution_work_item_id=701,
    correlation_id="corr-putaway-701",
    fact_version="resource-event:fact-701",
)


def _decision(operation_identity: str, *, fact_version: str = GROUP.fact_version) -> dict[str, object]:
    return WmsSyncObligationResolution(
        resolved_operation_identity=operation_identity,
        resolved_fact_version=fact_version,
        resolution="OBLIGATION_SATISFIED",
        source_event_id=f"resolution:{operation_identity}",
        evidence_reference=f"evidence:{operation_identity}",
    ).model_dump(mode="json")


def _obligation(
    operation_identity: str,
    *,
    intent_status: str,
    open_case: bool = False,
    decisions: tuple[dict[str, object], ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        operation_identity=operation_identity,
        fact_version=GROUP.fact_version,
        intent_status=intent_status,
        has_open_case=open_case,
        resolved_decisions=decisions,
    )


class _BarrierRepository:
    def __init__(self, obligations: tuple[SimpleNamespace, ...], *, hold_status: str = "OPEN") -> None:
        self.obligations = obligations
        self.hold = SimpleNamespace(
            id=91,
            hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
            status=hold_status,
            blocking=False,
            source_kind="WMS_SYNC_OBLIGATION",
            source_reason="WMS_PUTAWAY_SYNC_PENDING",
            evidence_snapshot_json={
                "barrier_kind": "WMS_PUTAWAY_E03_E07",
                "hold_scope": "OBJECT_ELIGIBILITY",
                "blocking": False,
                "execution_work_item_id": GROUP.execution_work_item_id,
                "correlation_id": GROUP.correlation_id,
                "fact_version": GROUP.fact_version,
                "required_operation_identities": list(WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES),
            },
        )
        self.resolve_count = 0

    async def load_group_for_update(self, _db: Any, group: WmsPutawaySyncBarrierGroup) -> Any:
        assert group == GROUP
        return SimpleNamespace(group=group, obligations=self.obligations)

    async def get_hold_for_update(self, _db: Any, *, source_idempotency_key: str) -> Any:
        assert source_idempotency_key
        return self.hold

    async def mark_hold_resolved(self, _db: Any, hold: Any, *, release_evidence: dict[str, object]) -> bool:
        assert release_evidence["fact_version"] == GROUP.fact_version
        if hold.status == RuntimeHoldStatus.RESOLVED:
            return False
        hold.status = RuntimeHoldStatus.RESOLVED
        self.resolve_count += 1
        return True


class _HoldRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    async def create_open_hold(self, _db: Any, **values: object) -> Any:
        self.created.append(values)
        return SimpleNamespace(id=91, **values)


class _DispatchRepository(_BarrierRepository):
    def __init__(self, identity: SimpleNamespace) -> None:
        super().__init__(())
        self.identity = identity
        self.calls: list[str] = []

    async def get_dispatch_identity(self, _db: Any, *, dispatch_key: str) -> Any:
        self.calls.append(f"identity:{dispatch_key}")
        return self.identity

    async def lock_group_mutex(
        self,
        _db: Any,
        *,
        execution_work_item_id: int,
        correlation_id: str,
    ) -> bool:
        assert execution_work_item_id == GROUP.execution_work_item_id
        assert correlation_id == GROUP.correlation_id
        self.calls.append("work-item")
        return True

    async def load_group_for_update(self, _db: Any, group: WmsPutawaySyncBarrierGroup) -> Any:
        self.calls.append("obligations")
        return await super().load_group_for_update(_db, group)


class _TriggerBarrier:
    def __init__(self, calls: list[str], *, fail_evaluation: bool = False) -> None:
        self.calls = calls
        self.fail_evaluation = fail_evaluation

    async def lock_group_for_dispatch(self, _db: Any, *, dispatch_key: str) -> WmsPutawaySyncBarrierGroup:
        self.calls.append(f"lock:{dispatch_key}")
        return GROUP

    async def evaluate_dispatch(
        self,
        _db: Any,
        *,
        dispatch_key: str,
        locked_group: WmsPutawaySyncBarrierGroup,
    ) -> Any:
        assert locked_group == GROUP
        self.calls.append(f"evaluate:{dispatch_key}")
        if self.fail_evaluation:
            raise RuntimeError("barrier evaluation failed")
        return SimpleNamespace(satisfied=True, released=True)


class _RecordingReducer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def reduce(self, _db: Any, event: EffectReducerEvent, **_kwargs: object) -> Any:
        self.calls.append(f"reduce:{event.event_type.value}")
        return SimpleNamespace(state_changed=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("e03", "e07"),
    [
        (_obligation(E03, intent_status="COMPLETED"), _obligation(E07, intent_status="COMPLETED")),
        (
            _obligation(E03, intent_status="COMPLETED"),
            _obligation(E07, intent_status="RECONCILING", decisions=(_decision(E07),)),
        ),
        (
            _obligation(E03, intent_status="RECONCILING", decisions=(_decision(E03),)),
            _obligation(E07, intent_status="COMPLETED"),
        ),
        (
            _obligation(E03, intent_status="RECONCILING", decisions=(_decision(E03),)),
            _obligation(E07, intent_status="RECONCILING", decisions=(_decision(E07),)),
        ),
    ],
)
async def test_all_completed_or_reconciled_2x2_combinations_release_once(
    e03: SimpleNamespace,
    e07: SimpleNamespace,
) -> None:
    repository = _BarrierRepository((e07, e03))
    service = WmsPutawaySyncBarrierService(repository=repository)

    first = await service.evaluate_and_release(object(), group=GROUP)
    second = await service.evaluate_and_release(object(), group=GROUP)

    assert first.satisfied is True
    assert first.released is True
    assert second.satisfied is True
    assert second.released is False
    assert repository.resolve_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "obligations",
    [
        (_obligation(E03, intent_status="COMPLETED"),),
        (
            _obligation(E03, intent_status="COMPLETED"),
            _obligation(E07, intent_status="COMPLETED", open_case=True),
        ),
        (
            _obligation(E03, intent_status="RECONCILING", decisions=(_decision(E07),)),
            _obligation(E07, intent_status="COMPLETED"),
        ),
        (
            _obligation(
                E03,
                intent_status="RECONCILING",
                decisions=(_decision(E03, fact_version="resource-event:other"),),
            ),
            _obligation(E07, intent_status="COMPLETED"),
        ),
    ],
)
async def test_incomplete_open_or_wrong_group_never_releases(
    obligations: tuple[SimpleNamespace, ...],
) -> None:
    repository = _BarrierRepository(obligations)

    result = await WmsPutawaySyncBarrierService(repository=repository).evaluate_and_release(object(), group=GROUP)

    assert result.satisfied is False
    assert result.released is False
    assert repository.resolve_count == 0


@pytest.mark.asyncio
async def test_create_hold_uses_internal_group_identity_without_vendor_contract() -> None:
    hold_repository = _HoldRepository()
    service = WmsPutawaySyncBarrierService(
        repository=_BarrierRepository(()),
        runtime_hold_repository=hold_repository,
    )

    await service.create_hold(
        object(),
        group=GROUP,
        workline_id=17,
        session_id=19,
        trace_id="trace-701",
    )

    created = hold_repository.created[0]
    assert created["source_kind"] == "WMS_SYNC_OBLIGATION"
    assert created["source_reason"] == "WMS_PUTAWAY_SYNC_PENDING"
    assert created["blocking"] is False
    assert created["workline_id"] == 17
    assert created["session_id"] == 19
    assert created["evidence_snapshot_json"] == {
        "barrier_kind": "WMS_PUTAWAY_E03_E07",
        "hold_scope": "OBJECT_ELIGIBILITY",
        "blocking": False,
        "execution_work_item_id": GROUP.execution_work_item_id,
        "correlation_id": GROUP.correlation_id,
        "fact_version": GROUP.fact_version,
        "required_operation_identities": list(WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES),
    }
    assert "command" not in str(created).lower()
    assert "task_type" not in str(created).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_hold", ["missing", "evidence", "voided", "blocking"])
async def test_invalid_object_eligibility_hold_fails_closed_before_obligation_evaluation(
    invalid_hold: str,
) -> None:
    repository = _BarrierRepository((_obligation(E03, intent_status="COMPLETED"),))
    if invalid_hold == "missing":
        repository.hold = None
    elif invalid_hold == "evidence":
        repository.hold.evidence_snapshot_json = {"fact_version": GROUP.fact_version}
    elif invalid_hold == "voided":
        repository.hold.status = RuntimeHoldStatus.VOIDED
    else:
        repository.hold.blocking = True

    with pytest.raises(RuntimeError, match="WMS sync barrier hold"):
        await WmsPutawaySyncBarrierService(repository=repository).evaluate_and_release(object(), group=GROUP)


@pytest.mark.asyncio
async def test_resolved_hold_with_unsatisfied_obligation_fails_closed() -> None:
    repository = _BarrierRepository(
        (_obligation(E03, intent_status="COMPLETED"),),
        hold_status=RuntimeHoldStatus.RESOLVED,
    )

    with pytest.raises(RuntimeError, match="resolved before all obligations"):
        await WmsPutawaySyncBarrierService(repository=repository).evaluate_and_release(object(), group=GROUP)


def test_group_requires_stable_internal_identity() -> None:
    with pytest.raises(ValueError, match="fact_version"):
        replace(GROUP, fact_version=" ")


@pytest.mark.asyncio
async def test_dispatch_group_lock_uses_work_item_mutex_before_obligation_rows() -> None:
    repository = _DispatchRepository(
        SimpleNamespace(
            operation_identity=E03,
            execution_work_item_id=GROUP.execution_work_item_id,
            correlation_id=GROUP.correlation_id,
            fact_version=GROUP.fact_version,
        )
    )

    group = await WmsPutawaySyncBarrierService(repository=repository).lock_group_for_dispatch(
        object(),
        dispatch_key="dispatch-e03",
    )

    assert group == GROUP
    assert repository.calls == ["identity:dispatch-e03", "work-item", "obligations"]


@pytest.mark.asyncio
async def test_non_obligation_dispatch_is_noop_before_any_lock() -> None:
    repository = _DispatchRepository(
        SimpleNamespace(
            operation_identity="wms.inventory.query@v1",
            execution_work_item_id=None,
            correlation_id=None,
            fact_version=None,
        )
    )

    result = await WmsPutawaySyncBarrierService(repository=repository).lock_group_for_dispatch(
        object(),
        dispatch_key="dispatch-query",
    )

    assert result is None
    assert repository.calls == ["identity:dispatch-query"]


@pytest.mark.asyncio
async def test_obligation_dispatch_with_incomplete_group_fails_closed() -> None:
    repository = _DispatchRepository(
        SimpleNamespace(
            operation_identity=E07,
            execution_work_item_id=GROUP.execution_work_item_id,
            correlation_id=GROUP.correlation_id,
            fact_version=None,
        )
    )

    with pytest.raises(RuntimeError, match="identity is incomplete"):
        await WmsPutawaySyncBarrierService(repository=repository).lock_group_for_dispatch(
            object(),
            dispatch_key="dispatch-e07",
        )

    assert repository.calls == ["identity:dispatch-e07"]


@pytest.mark.asyncio
async def test_transport_locks_before_reducer_and_evaluates_after_all_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(barrier_module, "wms_putaway_sync_barrier_service", _TriggerBarrier(calls))
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.SYNC_COMPLETED,
        dispatch_key="dispatch-e03",
        occurred_at_ms=1,
        source_event_id="transport-e03",
        evidence_json={},
    )

    await EffectTransportBridge(reducer=_RecordingReducer(calls)).record_result(
        object(),
        dispatch_key="dispatch-e03",
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=1,
        operation_identity=E03,
        resolution=EffectTransportResolution(events=(event, event)),
    )

    assert calls == [
        "lock:dispatch-e03",
        "reduce:SYNC_COMPLETED",
        "reduce:SYNC_COMPLETED",
        "evaluate:dispatch-e03",
    ]


@pytest.mark.asyncio
async def test_typed_reconciliation_locks_before_reducer_and_evaluates_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(barrier_module, "wms_putaway_sync_barrier_service", _TriggerBarrier(calls))

    await EffectReconciliationBridge(reducer=_RecordingReducer(calls)).resolve(
        object(),
        dispatch_key="dispatch-e07",
        occurred_at_ms=1,
        resolution=None,
        obligation_resolution=WmsSyncObligationResolution(
            resolved_operation_identity=E07,
            resolved_fact_version=GROUP.fact_version,
            resolution="OBLIGATION_SATISFIED",
            source_event_id="resolution-e07",
            evidence_reference="evidence-e07",
        ),
        reason_code="MANUAL",
        source_event_id="resolution-e07",
        evidence_json={},
    )

    assert calls == [
        "lock:dispatch-e07",
        "reduce:RECONCILIATION_RESOLVED",
        "evaluate:dispatch-e07",
    ]


@pytest.mark.asyncio
async def test_barrier_failure_propagates_before_owner_can_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        barrier_module,
        "wms_putaway_sync_barrier_service",
        _TriggerBarrier(calls, fail_evaluation=True),
    )
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.SYNC_COMPLETED,
        dispatch_key="dispatch-e03",
        occurred_at_ms=1,
        source_event_id="transport-e03",
        evidence_json={},
    )

    with pytest.raises(RuntimeError, match="barrier evaluation failed"):
        await EffectTransportBridge(reducer=_RecordingReducer(calls)).record_result(
            object(),
            dispatch_key="dispatch-e03",
            attempt_no=1,
            result=SimpleNamespace(),
            retry_exhausted=False,
            occurred_at_ms=1,
            operation_identity=E03,
            resolution=EffectTransportResolution(events=(event,)),
        )

    assert calls == [
        "lock:dispatch-e03",
        "reduce:SYNC_COMPLETED",
        "evaluate:dispatch-e03",
    ]
