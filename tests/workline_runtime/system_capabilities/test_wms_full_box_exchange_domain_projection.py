"""E11 preparation/reducer/projector 的纯逻辑 RED。"""

from __future__ import annotations

from datetime import UTC
from importlib import import_module
from importlib.util import find_spec
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import WmsFulfillmentDomainProjector
from src.app.runtime.system_capabilities.wms.effect_runtime import WmsEffectPreparationRuntime
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.wms_integration.effect_runtime import typed_wms_effect_ack_hash
from src.app.wms_integration.operation_contract import WmsDomainProjectionKind
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.effect_status import WmsEffectStatus, WmsEffectStatusSnapshot
from src.app.wms_integration.ports.fulfillment_operations import (
    FullBoxExchangeResult,
    WmsEffectAck,
)
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES
from tests.workline_runtime.system_capabilities.test_wms_effect_status_service import (
    NOW,
    _claim,
    _Db,
    _Port,
    _ReconciliationBridge,
    _Reducer,
    _Repository,
    _settings,
)

E11 = "wms.fulfillment.full_box_exchange@v1"


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
                "db": db,
                "operation": operation,
                "request": request,
                "intent_id": execution.intent_log.id,
            }
        )


class _ReconciliationProjector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def project_reconciliation_opened(
        self,
        db: Any,
        *,
        operation: Any,
        dispatch_key: str,
    ) -> None:
        assert db.commits == 1, "parent demand 必须与 reconciliation case 在最终 commit 前同事务投影"
        self.calls.append({"operation": operation, "dispatch_key": dispatch_key})


def _e11_preparation() -> tuple[Any, Any, SimpleNamespace]:
    operation = WMS_OPERATION_BY_IDENTITY[E11]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[E11])
    db = _PreparationDb()
    execution = SimpleNamespace(
        db=db,
        ctx={
            "session": SimpleNamespace(id=11),
            "workline": SimpleNamespace(id=22),
            "trace_id": "trace-e11",
            "wms_full_box_exchange_claim": SimpleNamespace(
                handoff_demand_id=41,
                full_box_id=request.full_box_id,
                exchange_request_key=request.exchange_request_key,
            ),
        },
        intent=SimpleNamespace(operation_key=request.exchange_request_key),
        intent_log=SimpleNamespace(id=17, dispatch_key=request.dispatch_key),
        idempotency_key=request.exchange_request_key,
    )
    return operation, request, execution


@pytest.mark.asyncio
async def test_e11_preparation_uses_existing_domain_hook_before_outbox() -> None:
    operation, request, execution = _e11_preparation()
    projector = _RecordingProjector(execution.db.events)

    await WmsEffectPreparationRuntime(
        catalog=build_provider_catalog(),
        domain_projector=projector,
    ).prepare(operation, request, execution=execution)

    assert operation.domain_projection_kind is not None
    assert execution.db.events == ["domain:prepare", "outbox:add", "outbox:flush"]
    assert projector.calls[0]["intent_id"] == 17


def _e11_status_claim() -> Any:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    request_payload = dict(REQUEST_FIXTURES[E11])
    canonical = CanonicalPayload.from_projection(request_payload)
    ack = WmsEffectAck(
        operation_identity=E11,
        idempotency_key="idem-e11",
        provider_reference="provider-e11",
        submission_state="ACCEPTED",
    )
    ack_hash = typed_wms_effect_ack_hash(ack)
    claim.intent.dispatch_key = request_payload["dispatch_key"]
    claim.intent.idempotency_key = ack.idempotency_key
    claim.intent.capability_key = "wms.fulfillment.full_box_exchange"
    claim.intent.operation_identity = request_payload["exchange_request_key"]
    claim.intent.payload_hash = canonical.sha256
    claim.intent.outcome_history_json = [
        {
            "event_type": EffectReducerEventType.TRANSPORT_ACCEPTED.value,
            "typed_ack_hash": ack_hash,
            "typed_ack_reference": f"runtime-intent-outcome:{request_payload['dispatch_key']}",
        }
    ]
    claim.intent.outcome_json = {
        "payload_hash": canonical.sha256,
        "outcome": {"kind": "success", "payload": ack.model_dump(mode="json")},
    }
    claim.outbox.dispatch_key = request_payload["dispatch_key"]
    claim.outbox.idempotency_key = ack.idempotency_key
    claim.outbox.operation_identity = E11
    claim.outbox.payload_json = request_payload
    claim.outbox.payload_hash = canonical.sha256
    claim.outbox.canonical_payload_bytes = canonical.body
    return claim


def _e11_non_success_snapshot() -> WmsEffectStatusSnapshot:
    request = WMS_OPERATION_BY_IDENTITY[E11].request_model.model_validate(REQUEST_FIXTURES[E11])
    result = FullBoxExchangeResult(
        dispatch_key=request.dispatch_key,
        provider_reference="provider-e11",
        source_version="7",
        exchange_request_key=request.exchange_request_key,
        full_box_id=request.full_box_id,
        selected_empty_box_id="EMPTY-1",
        full_box_destination={
            "rack_id": "FIVE-RACK-1",
            "bin_id": request.full_box_id,
            "slot_id": "FIVE-SLOT-1",
        },
        empty_box_destination={
            "rack_id": request.rack_id,
            "bin_id": "EMPTY-1",
            "slot_id": request.source_slot_id,
        },
        final_relations=[
            {
                "rack_id": "FIVE-RACK-1",
                "bin_id": request.full_box_id,
                "slot_id": "FIVE-SLOT-1",
            },
            {
                "rack_id": request.rack_id,
                "bin_id": "EMPTY-1",
                "slot_id": request.source_slot_id,
            },
        ],
        task_outcome="PARTIAL_FAILURE",
        inventory_source_version="inventory-7",
    )
    return WmsEffectStatusSnapshot(
        operation_identity=E11,
        idempotency_key="idem-e11",
        state=WmsEffectStatus.COMPLETED,
        provider_reference="provider-e11",
        updated_at=NOW.replace(tzinfo=UTC),
        source_version=7,
        result=result,
    )


@pytest.mark.asyncio
async def test_e11_non_success_opens_reconciliation_before_reducer() -> None:
    claim = _e11_status_claim()
    db = _Db()
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    projector = _ReconciliationProjector()
    snapshot = _e11_non_success_snapshot()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        port_factory_builder=lambda _binding: lambda: _Port(snapshot, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
        domain_projector=projector,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert reducer.events == []
    assert reconciliation.calls[0]["reason_code"] == "WMS_FULFILLMENT_TERMINAL_NON_SUCCESS"
    assert reconciliation.calls[0]["evidence_json"]["operation_identity"] == E11
    assert reconciliation.calls[0]["evidence_json"]["task_outcome"] == "PARTIAL_FAILURE"
    assert projector.calls == [
        {
            "operation": WMS_OPERATION_BY_IDENTITY[E11],
            "dispatch_key": claim.intent.dispatch_key,
        }
    ]
    assert db.commits == 2


@pytest.mark.asyncio
async def test_non_e11_reconciliation_does_not_project_full_box_parent() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    db = _Db()
    projector = _ReconciliationProjector()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_Reducer(),
        reconciliation_bridge=_ReconciliationBridge(),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
        domain_projector=projector,
    )

    result = await service._open_reconciliation(
        db,
        claim=claim,
        reason_code="TEST_NON_E11",
        evidence={},
    )

    assert result.outcome == "RECONCILING"
    assert projector.calls == []


@pytest.mark.asyncio
async def test_e11_reconciliation_projector_preserves_active_intent_and_only_moves_parent_status() -> None:
    demand = SimpleNamespace(
        status="WAITING_FULL_BOX_EXCHANGE",
        active_full_box_exchange_intent_id=17,
    )

    class _FullBoxExchangeLookup:
        async def get_demand_by_dispatch_for_update(self, _db: Any, *, dispatch_key: str) -> Any:
            assert dispatch_key == "dispatch-exchange-001"
            return demand

    db = _PreparationDb()
    await WmsFulfillmentDomainProjector(
        full_box_exchange=_FullBoxExchangeLookup(),
    ).project_reconciliation_opened(
        db,
        operation=WMS_OPERATION_BY_IDENTITY[E11],
        dispatch_key="dispatch-exchange-001",
    )

    assert demand.status == "RECONCILING"
    assert demand.active_full_box_exchange_intent_id == 17
    assert db.events == ["outbox:flush"]


@pytest.mark.asyncio
async def test_e11_reject_keeps_active_intent_and_moves_parent_to_reconciling() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E11].model_copy(
        update={"domain_projection_kind": WmsDomainProjectionKind.FULL_BOX_EXCHANGE_DEMAND}
    )
    request_payload = dict(REQUEST_FIXTURES[E11])
    demand = SimpleNamespace(
        status="WAITING_FULL_BOX_EXCHANGE",
        active_full_box_exchange_intent_id=17,
        lifecycle_state="ACTIVE",
        closed_at_ms=None,
    )
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.STATUS_REJECTED,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=1,
        source_event_id="e11-rejected",
        reason_code="NO_EMPTY_BOX_AVAILABLE",
        evidence_json={},
    )

    await WmsFulfillmentDomainProjector()._project_reject(
        SimpleNamespace(flush=lambda: None),
        demand=demand,
        operation=operation,
        request_payload=request_payload,
        event=event,
    )

    assert demand.status == "RECONCILING"
    assert demand.active_full_box_exchange_intent_id == 17
    assert demand.lifecycle_state == "ACTIVE"


def test_e11_stable_dispatch_identity_is_parent_demand_and_full_bin() -> None:
    module_name = "src.app.runtime.orchestration.services.full_box_exchange_service"
    assert find_spec(module_name) is not None, "FullBoxExchangeService module is missing"
    service_module = import_module(module_name)
    service_type = getattr(service_module, "FullBoxExchangeService", None)

    assert service_type is not None
    assert service_type.exchange_request_key(handoff_demand_id=41, full_box_id="FULL-1") == "wms-e11:41:FULL-1"
    assert len(service_type.exchange_request_key(handoff_demand_id=41, full_box_id="F" * 120)) <= 160
