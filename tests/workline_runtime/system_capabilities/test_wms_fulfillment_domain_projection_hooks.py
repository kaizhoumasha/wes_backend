"""E08/E09 履约投影必须挂在现有 Intent/Outbox/reducer 事务边界。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.app.runtime.orchestration.effect_bridges import EffectTransportBridge, EffectTransportResolution
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.system_capabilities.outcomes import BusinessReject
from src.app.runtime.system_capabilities.wms.effect_runtime import WmsEffectPreparationRuntime
from src.app.sys.external_http_transport import ExternalHttpProtocolResult, ExternalHttpTransportResult
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.effect_status import WmsEffectStatus
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES
from tests.workline_runtime.system_capabilities.test_wms_effect_status_service import (
    NOW,
    _claim,
    _Port,
    _ReconciliationBridge,
    _Repository,
    _settings,
    _snapshot,
)
from tests.workline_runtime.system_capabilities.test_wms_effect_status_service import (
    _Db as _StatusDb,
)

E08 = "wms.fulfillment.request_rack_supply@v1"


class _PreparationDb:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.events.append("outbox:add")
        self.added.append(value)

    async def flush(self) -> None:
        self.events.append("outbox:flush")


class _RecordingProjector:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    async def prepare_effect(self, db: Any, *, operation: Any, request: Any, execution: Any) -> None:
        self.events.append("domain:prepare")
        self.calls.append(
            {
                "kind": operation.domain_projection_kind,
                "request": request,
                "intent_id": execution.intent_log.id,
            }
        )
        assert db is execution.db

    async def project_event(
        self,
        db: Any,
        *,
        operation: Any,
        request_payload: dict[str, Any],
        event: EffectReducerEvent,
        reduction: Any,
    ) -> None:
        self.events.append("domain:project")
        self.calls.append(
            {
                "operation": operation,
                "request_payload": request_payload,
                "event": event,
                "reduction": reduction,
                "db": db,
            }
        )


class _RecordingReducer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reduced: list[EffectReducerEvent] = []

    async def reduce(self, _db: Any, event: EffectReducerEvent, **_kwargs: Any) -> SimpleNamespace:
        self.events.append("reducer")
        self.reduced.append(event)
        return SimpleNamespace(state_changed=True, contradiction=False)


def _e08_preparation() -> tuple[Any, Any, SimpleNamespace]:
    operation = WMS_OPERATION_BY_IDENTITY[E08]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[E08])
    db = _PreparationDb()
    execution = SimpleNamespace(
        db=db,
        ctx={
            "session": SimpleNamespace(id=11),
            "workline": SimpleNamespace(id=22),
            "trace_id": "trace-domain",
            "wms_rack_demand_claim": SimpleNamespace(
                demand_id=41,
                demand_generation=request.demand_generation,
            ),
        },
        intent=SimpleNamespace(operation_key="rack-demand:41"),
        intent_log=SimpleNamespace(id=17, dispatch_key=request.dispatch_key),
        idempotency_key="rack-demand:41",
    )
    return operation, request, execution


@pytest.mark.asyncio
async def test_domain_preparation_runs_after_intent_claim_and_before_outbox_write() -> None:
    operation, request, execution = _e08_preparation()
    projector = _RecordingProjector(execution.db.events)

    await WmsEffectPreparationRuntime(
        catalog=build_provider_catalog(),
        domain_projector=projector,
    ).prepare(operation, request, execution=execution)

    assert execution.db.events == ["domain:prepare", "outbox:add", "outbox:flush"]
    assert projector.calls == [
        {
            "kind": operation.domain_projection_kind,
            "request": request,
            "intent_id": 17,
        }
    ]


@pytest.mark.asyncio
async def test_domain_operation_without_a_projector_binding_fails_before_outbox() -> None:
    operation, request, execution = _e08_preparation()

    with pytest.raises(RuntimeError, match="domain projector"):
        await WmsEffectPreparationRuntime(catalog=build_provider_catalog()).prepare(
            operation,
            request,
            execution=execution,
        )

    assert execution.db.events == []
    assert execution.db.added == []


@pytest.mark.asyncio
async def test_initial_async_submit_reject_projects_after_the_unique_reducer_without_commit() -> None:
    events: list[str] = []
    reducer = _RecordingReducer(events)
    projector = _RecordingProjector(events)
    bridge = EffectTransportBridge(reducer=reducer, domain_projector=projector)
    request_payload = REQUEST_FIXTURES[E08]
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=1,
        source_event_id="reject-event",
        attempt_no=1,
        reason_code="NO_RACK_AVAILABLE",
        evidence_json={},
    )
    db = SimpleNamespace(commit=lambda: pytest.fail("transport bridge must not commit"))

    await bridge.record_result(
        db,
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        result=ExternalHttpTransportResult.accepted(
            http_status_code=409,
            protocol_result=ExternalHttpProtocolResult.REJECTED,
            response_body=b"{}",
        ),
        retry_exhausted=False,
        occurred_at_ms=1,
        operation_identity=E08,
        payload_json=request_payload,
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == ["reducer", "domain:project"]
    assert projector.calls[0]["event"] is event
    assert projector.calls[0]["request_payload"] == request_payload


@pytest.mark.asyncio
async def test_status_terminal_projects_after_reducer_and_before_the_existing_commit() -> None:
    events: list[str] = []
    claim = _claim()
    db = _StatusDb()
    reducer = _RecordingReducer(events)
    projector = _RecordingProjector(events)
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        domain_projector=projector,
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.COMPLETED), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "COMPLETED"
    assert events == ["reducer", "domain:project"]
    assert db.commits == 2
    assert projector.calls[0]["event"].event_type is EffectReducerEventType.STATUS_COMPLETED


@pytest.mark.asyncio
async def test_domain_non_success_completed_opens_reconciliation_before_terminal_reducer() -> None:
    events: list[str] = []
    claim = _claim()
    db = _StatusDb()
    reducer = _RecordingReducer(events)
    projector = _RecordingProjector(events)
    reconciliation = _ReconciliationBridge()
    snapshot = _snapshot(WmsEffectStatus.COMPLETED)
    assert snapshot.result is not None
    snapshot = snapshot.model_copy(
        update={
            "result": snapshot.result.model_copy(
                update={"task_outcome": "FAILED_AFTER_EXECUTION"},
            )
        }
    )
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        domain_projector=projector,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(snapshot, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert events == []
    assert reducer.reduced == []
    assert projector.calls == []
    assert reconciliation.calls[0]["reason_code"] == "WMS_FULFILLMENT_TERMINAL_NON_SUCCESS"
    assert reconciliation.calls[0]["evidence_json"]["operation_identity"] == E08
    assert reconciliation.calls[0]["evidence_json"]["task_outcome"] == "FAILED_AFTER_EXECUTION"


@pytest.mark.asyncio
async def test_status_nonterminal_does_not_run_the_fulfillment_terminal_projector() -> None:
    events: list[str] = []
    claim = _claim()
    db = _StatusDb()
    projector = _RecordingProjector(events)
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_RecordingReducer(events),
        domain_projector=projector,
        reconciliation_bridge=_ReconciliationBridge(),
        port_factory_builder=lambda _binding: lambda: _Port(_snapshot(WmsEffectStatus.PROCESSING), db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "PROCESSING"
    assert events == ["reducer"]
    assert projector.calls == []


@pytest.mark.asyncio
async def test_resubmit_business_reject_projects_in_the_same_session_before_commit() -> None:
    events: list[str] = []
    claim = _claim()
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = None
    db = _StatusDb()
    projector = _RecordingProjector(events)
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_RecordingReducer(events),
        domain_projector=projector,
        reconciliation_bridge=_ReconciliationBridge(),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )
    result = ExternalHttpTransportResult.accepted(
        http_status_code=409,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        response_body=b"{}",
    )
    reject = BusinessReject(
        reason_code="NO_RACK_AVAILABLE",
        message="no rack",
        details={"typed_reject_hash": "a" * 64},
    )

    with patch(
        "src.app.runtime.orchestration.services.wms_effect_status_service.interpret_async_effect_ack_response",
        return_value=reject,
    ):
        recorded = await service._record_resubmit_result(
            db,
            claim=claim,
            result=result,
            evidence={"recovery": "original-key"},
        )

    assert recorded.outcome == "REJECTED"
    assert events == ["reducer", "domain:project"]
    assert db.commits == 1
    assert projector.calls[0]["event"].event_type is EffectReducerEventType.ASYNC_SUBMIT_REJECTED
