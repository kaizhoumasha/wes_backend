"""E13 status terminal 必须保留 reducer 前冻结 ACK 并走唯一领域 delegate。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
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

E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


class _RecordingReducer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reduced: list[Any] = []

    async def reduce(self, _db: Any, event: Any, **_kwargs: Any) -> SimpleNamespace:
        self.events.append("reducer")
        self.reduced.append(event)
        return SimpleNamespace(state_changed=True, contradiction=False)


class _StatefulReducer:
    def __init__(self, events: list[str], intent: Any) -> None:
        self.events = events
        self.intent = intent
        self.seen: set[str] = set()

    async def reduce(self, _db: Any, event: Any, **_kwargs: Any) -> SimpleNamespace:
        self.events.append(f"reducer:{event.event_type.value}")
        if event.source_event_id in self.seen:
            return SimpleNamespace(state_changed=False, contradiction=False)
        self.seen.add(event.source_event_id)
        self.intent.outcome_history_json.append(
            {
                "event_type": event.event_type.value,
                **event.evidence_json,
            }
        )
        return SimpleNamespace(state_changed=True, contradiction=False)


class _RecordingReconciliation:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def open(self, _db: Any, **_kwargs: Any) -> SimpleNamespace:
        self.events.append("reconciliation")
        return SimpleNamespace(state_changed=True, case_created=True)


class _RecordingProjector:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.terminal_calls: list[dict[str, Any]] = []
        self.reconciliation_calls: list[dict[str, Any]] = []

    async def project_event(self, _db: Any, **kwargs: Any) -> None:
        self.events.append("domain:terminal")
        self.terminal_calls.append(kwargs)

    async def project_reconciliation_opened(self, _db: Any, **kwargs: Any) -> None:
        self.events.append("domain:reconciliation")
        self.reconciliation_calls.append(kwargs)

    async def should_reconcile_status_reject(self, _db: Any, **kwargs: Any) -> bool:
        self.events.append("domain:reject-preflight")
        return kwargs.get("frozen_ack") is not None


class _RecoveredAckProjector(_RecordingProjector):
    async def should_reconcile_ack(self, _db: Any, **_kwargs: Any) -> bool:
        self.events.append("domain:ack-preflight")
        return False

    async def project_event(self, _db: Any, **kwargs: Any) -> None:
        if kwargs["event"].event_type is EffectReducerEventType.TRANSPORT_ACCEPTED:
            self.events.append("domain:ack")
        else:
            self.events.append("domain:terminal")
            self.terminal_calls.append(kwargs)


def _e13_claim_and_snapshot(*, task_outcome: str) -> tuple[Any, WmsEffectAck, WmsEffectStatusSnapshot]:
    request_payload = deepcopy(REQUEST_FIXTURES[E13])
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    idempotency_key = "idem-e13-status"
    ack = WmsEffectAck.model_validate(
        build_typed_ack(E13, idempotency_key, request_payload, submission_state="ACCEPTED")
    )
    canonical = CanonicalPayload.from_projection(request_payload)
    claim.intent.dispatch_key = request_payload["dispatch_key"]
    claim.intent.idempotency_key = idempotency_key
    claim.intent.capability_key = "wms.fulfillment.move_bins_from_conveyor_exit"
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
    claim.outbox.operation_identity = E13
    claim.outbox.payload_json = request_payload
    claim.outbox.payload_hash = canonical.sha256
    claim.outbox.canonical_payload_bytes = canonical.body

    result_payload = deepcopy(
        build_typed_result(
            E13,
            request_payload,
            source_version=8,
            completed_at="2026-07-30T09:04:00+00:00",
            provider_reference=ack.provider_reference,
        )
    )
    if task_outcome != "SUCCESS":
        result_payload["task_outcome"] = task_outcome
        result_payload["items"][0]["item_outcome"] = "UNKNOWN"
        result_payload["items"][0]["final_rack_id"] = None
        result_payload["items"][0]["final_slot_id"] = None
    snapshot = WmsEffectStatusSnapshot(
        operation_identity=E13,
        idempotency_key=idempotency_key,
        state=WmsEffectStatus.COMPLETED,
        provider_reference=ack.provider_reference,
        updated_at=NOW.replace(tzinfo=UTC),
        source_version=8,
        result=WMS_OPERATION_BY_IDENTITY[E13].result_model.model_validate(result_payload),
    )
    return claim, ack, snapshot


@pytest.mark.asyncio
async def test_e13_success_status_preserves_frozen_ack_across_terminal_reducer() -> None:
    events: list[str] = []
    claim, ack, snapshot = _e13_claim_and_snapshot(task_outcome="SUCCESS")
    projector = _RecordingProjector(events)
    db = _Db()
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
    assert projector.terminal_calls[0]["frozen_ack"] == ack


@pytest.mark.asyncio
async def test_e13_partial_status_opens_case_before_reconciliation_with_frozen_ack() -> None:
    events: list[str] = []
    claim, ack, snapshot = _e13_claim_and_snapshot(task_outcome="FAILED_AFTER_EXECUTION")
    projector = _RecordingProjector(events)
    db = _Db()
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

    assert result.outcome == "RECONCILING"
    assert events == ["reconciliation", "domain:reconciliation"]
    assert projector.reconciliation_calls[0]["frozen_ack"] == ack
    assert projector.reconciliation_calls[0]["evidence_json"]["snapshot"]["result"]["task_outcome"] == (
        "FAILED_AFTER_EXECUTION"
    )


@pytest.mark.asyncio
async def test_e13_status_reject_after_persisted_ack_never_releases_pristine_domain_claims() -> None:
    events: list[str] = []
    claim, ack, completed = _e13_claim_and_snapshot(task_outcome="SUCCESS")
    snapshot = WmsEffectStatusSnapshot(
        operation_identity=E13,
        idempotency_key=claim.intent.idempotency_key,
        state=WmsEffectStatus.REJECTED,
        provider_reference=completed.provider_reference,
        updated_at=NOW.replace(tzinfo=UTC),
        source_version=9,
        reason_code="NO_DESTINATION_CAPACITY",
    )
    projector = _RecordingProjector(events)
    db = _Db()
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

    assert result.outcome == "RECONCILING"
    assert events == ["domain:reject-preflight", "reconciliation", "domain:reconciliation"]
    assert projector.reconciliation_calls[0]["frozen_ack"] == ack
    assert projector.reconciliation_calls[0]["reason_code"] == "WMS_E13_REJECT_AFTER_ACK_OR_PHYSICAL_EVIDENCE"


@pytest.mark.asyncio
async def test_e13_completed_status_first_recovered_ack_runs_ack_then_terminal_once() -> None:
    events: list[str] = []
    claim, ack, snapshot = _e13_claim_and_snapshot(task_outcome="SUCCESS")
    claim.intent.effect_status = RuntimeIntentStatus.PROPOSED
    claim.intent.outcome_json = {}
    claim.intent.outcome_history_json = []
    snapshot = snapshot.model_copy(update={"recovered_ack": ack})
    projector = _RecoveredAckProjector(events)
    reducer = _StatefulReducer(events, claim.intent)
    db = _Db()
    service = WmsEffectStatusService(
        repository=_Repository(claim),
        reducer=reducer,
        reconciliation_bridge=_RecordingReconciliation(events),
        domain_projector=projector,
        port_factory_builder=lambda _binding: lambda: _Port(snapshot, db),
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    first = await service.check_dispatch(db, dispatch_key=claim.intent.dispatch_key)

    assert first.outcome == "COMPLETED"
    assert events == [
        "domain:ack-preflight",
        "reducer:TRANSPORT_ACCEPTED",
        "domain:ack",
        "reducer:STATUS_COMPLETED",
        "domain:terminal",
    ]
    assert projector.terminal_calls[0]["frozen_ack"] == ack
