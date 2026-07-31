"""E12 status terminal 必须在唯一 reducer/reconciliation 边界内投影。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.wms_integration.effect_runtime import typed_wms_effect_ack_hash
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.effect_status import WmsEffectStatus, WmsEffectStatusSnapshot
from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES
from tests.workline_runtime.system_capabilities.test_wms_effect_status_service import (
    NOW,
    _claim,
    _Db,
    _Port,
    _Repository,
    _settings,
)

E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"


class _RecordingReducer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reduced: list[Any] = []

    async def reduce(self, _db: Any, event: Any, **_kwargs: Any) -> SimpleNamespace:
        self.events.append("reducer")
        self.reduced.append(event)
        return SimpleNamespace(state_changed=True, contradiction=False)


class _RecordingReconciliation:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    async def open(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.events.append("reconciliation")
        self.calls.append(kwargs)
        return SimpleNamespace(state_changed=True)


class _RecordingProjector:
    def __init__(self, events: list[str], *, reject_requires_reconciliation: bool = False) -> None:
        self.events = events
        self.reject_requires_reconciliation = reject_requires_reconciliation
        self.terminal_calls: list[dict[str, Any]] = []
        self.reconciliation_calls: list[dict[str, Any]] = []

    async def project_event(self, _db: Any, **kwargs: Any) -> None:
        self.events.append("domain:terminal")
        self.terminal_calls.append(kwargs)

    async def project_reconciliation_opened(self, _db: Any, **kwargs: Any) -> None:
        self.events.append("domain:reconciliation")
        self.reconciliation_calls.append(kwargs)

    async def should_reconcile_status_reject(self, _db: Any, **_kwargs: Any) -> bool:
        self.events.append("domain:reject-preflight")
        return self.reject_requires_reconciliation


def _e12_claim_and_snapshot(*, task_outcome: str) -> tuple[Any, WmsEffectStatusSnapshot]:
    request_payload = deepcopy(REQUEST_FIXTURES[E12])
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    idempotency_key = "idem-e12-status"
    ack = WmsEffectAck.model_validate(
        build_typed_ack(E12, idempotency_key, request_payload, submission_state="ACCEPTED")
    )
    canonical = CanonicalPayload.from_projection(request_payload)
    claim.intent.dispatch_key = request_payload["dispatch_key"]
    claim.intent.idempotency_key = idempotency_key
    claim.intent.capability_key = "wms.fulfillment.move_bins_to_conveyor_entry"
    claim.intent.capability_contract_version = "v1"
    claim.intent.payload_hash = canonical.sha256
    claim.intent.outcome_history_json = [
        {
            "event_type": "TRANSPORT_ACCEPTED",
            "typed_ack_hash": typed_wms_effect_ack_hash(ack),
            "typed_ack_reference": f"runtime-intent-outcome:{request_payload['dispatch_key']}",
        }
    ]
    claim.intent.outcome_json = {
        "payload_hash": canonical.sha256,
        "outcome": {"kind": "success", "payload": ack.model_dump(mode="json")},
    }
    claim.outbox.dispatch_key = request_payload["dispatch_key"]
    claim.outbox.idempotency_key = idempotency_key
    claim.outbox.operation_identity = E12
    claim.outbox.payload_json = request_payload
    claim.outbox.payload_hash = canonical.sha256
    claim.outbox.canonical_payload_bytes = canonical.body

    result_payload = deepcopy(
        build_typed_result(
            E12,
            request_payload,
            source_version=8,
            completed_at="2026-07-30T09:04:00+00:00",
            provider_reference=ack.provider_reference,
        )
    )
    if task_outcome != "SUCCESS":
        result_payload["task_outcome"] = task_outcome
        result_payload["items"][0]["item_outcome"] = "FAILED"
    snapshot = WmsEffectStatusSnapshot(
        operation_identity=E12,
        idempotency_key=idempotency_key,
        state=WmsEffectStatus.COMPLETED,
        provider_reference=ack.provider_reference,
        updated_at=NOW.replace(tzinfo=UTC),
        source_version=8,
        result=WMS_OPERATION_BY_IDENTITY[E12].result_model.model_validate(result_payload),
    )
    return claim, snapshot


@pytest.mark.asyncio
async def test_e12_success_status_uses_terminal_reducer_then_domain_projector() -> None:
    events: list[str] = []
    claim, snapshot = _e12_claim_and_snapshot(task_outcome="SUCCESS")
    db = _Db()
    projector = _RecordingProjector(events)
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=_RecordingReducer(events),
        reconciliation_bridge=_RecordingReconciliation(events),
        domain_projector=projector,
        port_factory_builder=lambda _binding: lambda: _Port(snapshot, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "COMPLETED"
    assert events == ["reducer", "domain:terminal"]
    assert projector.reconciliation_calls == []


@pytest.mark.asyncio
async def test_e12_partial_terminal_opens_case_before_domain_projection_without_completed_reducer() -> None:
    events: list[str] = []
    claim, snapshot = _e12_claim_and_snapshot(task_outcome="FAILED_AFTER_EXECUTION")
    db = _Db()
    reducer = _RecordingReducer(events)
    reconciliation = _RecordingReconciliation(events)
    projector = _RecordingProjector(events)
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        domain_projector=projector,
        port_factory_builder=lambda _binding: lambda: _Port(snapshot, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert events == ["reconciliation", "domain:reconciliation"]
    assert reducer.reduced == []
    assert reconciliation.calls[0]["reason_code"] == "WMS_FULFILLMENT_TERMINAL_NON_SUCCESS"
    assert projector.reconciliation_calls[0]["reason_code"] == "WMS_FULFILLMENT_TERMINAL_NON_SUCCESS"
    assert projector.reconciliation_calls[0]["evidence_json"]["snapshot"]["result"]["task_outcome"] == (
        "FAILED_AFTER_EXECUTION"
    )


@pytest.mark.asyncio
async def test_e12_late_status_reject_opens_case_before_rejected_reducer() -> None:
    events: list[str] = []
    claim, completed = _e12_claim_and_snapshot(task_outcome="SUCCESS")
    snapshot = WmsEffectStatusSnapshot(
        operation_identity=E12,
        idempotency_key=claim.intent.idempotency_key,
        state=WmsEffectStatus.REJECTED,
        provider_reference=completed.provider_reference,
        updated_at=NOW.replace(tzinfo=UTC),
        source_version=9,
        reason_code="CONVEYOR_ENTRY_CAPACITY_CHANGED",
    )
    db = _Db()
    reducer = _RecordingReducer(events)
    reconciliation = _RecordingReconciliation(events)
    projector = _RecordingProjector(events, reject_requires_reconciliation=True)
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        domain_projector=projector,
        port_factory_builder=lambda _binding: lambda: _Port(snapshot, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert result.outcome == "RECONCILING"
    assert events == [
        "domain:reject-preflight",
        "reconciliation",
        "domain:reconciliation",
    ]
    assert reducer.reduced == []
    assert reconciliation.calls[0]["reason_code"] == "WMS_E12_REJECT_AFTER_PHYSICAL_EVIDENCE"
