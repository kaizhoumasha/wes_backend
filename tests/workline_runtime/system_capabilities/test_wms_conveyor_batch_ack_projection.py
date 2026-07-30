"""E12 整批 ACK、恢复 ACK 与立即拒绝的领域投影合同。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.effect_bridges import EffectTransportBridge, EffectTransportResolution
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    WmsFulfillmentDomainProjector,
)
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"
E08 = "wms.fulfillment.request_rack_supply@v1"


class _RecordingReducer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reduced: list[EffectReducerEvent] = []

    async def reduce(self, _db: Any, event: EffectReducerEvent, **_kwargs: Any) -> SimpleNamespace:
        self.events.append("reducer")
        self.reduced.append(event)
        return SimpleNamespace(state_changed=True, contradiction=False)


class _RecordingBatchService:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.ack_calls: list[dict[str, Any]] = []
        self.reject_calls: list[dict[str, Any]] = []
        self.success_calls: list[dict[str, Any]] = []
        self.reconciliation_calls: list[dict[str, Any]] = []

    async def project_ack(self, db: Any, **kwargs: Any) -> None:
        self.events.append("batch:ack")
        self.ack_calls.append({"db": db, **kwargs})

    async def project_reject(self, db: Any, **kwargs: Any) -> None:
        self.events.append("batch:reject")
        self.reject_calls.append({"db": db, **kwargs})

    async def project_success(self, db: Any, **kwargs: Any) -> None:
        self.events.append("batch:success")
        self.success_calls.append({"db": db, **kwargs})

    async def project_reconciliation_opened(self, db: Any, **kwargs: Any) -> None:
        self.events.append("batch:reconciliation")
        self.reconciliation_calls.append({"db": db, **kwargs})


class _RecordingProjector:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []
        self.reconciliation_calls: list[dict[str, Any]] = []

    async def project_event(self, db: Any, **kwargs: Any) -> None:
        self.events.append("projector")
        self.calls.append({"db": db, **kwargs})

    async def project_reconciliation_opened(self, db: Any, **kwargs: Any) -> None:
        self.events.append("projector:reconciliation")
        self.reconciliation_calls.append({"db": db, **kwargs})


def _request_payload() -> dict[str, Any]:
    return dict(REQUEST_FIXTURES[E12])


def _ack(request_payload: dict[str, Any]) -> WmsEffectAck:
    return WmsEffectAck.model_validate(build_typed_ack(E12, "idem-e12", request_payload, submission_state="ACCEPTED"))


def _ack_event(request_payload: dict[str, Any]) -> EffectReducerEvent:
    ack = _ack(request_payload)
    return EffectReducerEvent(
        event_type=EffectReducerEventType.TRANSPORT_ACCEPTED,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=101,
        source_event_id="e12-ack-event",
        attempt_no=1,
        reason_code="WMS_ASYNC_ACK_ACCEPTED",
        evidence_json={"typed_ack_hash": "a" * 64},
        terminal_outcome={"kind": "success", "payload": ack.model_dump(mode="json")},
    )


@pytest.mark.asyncio
async def test_e12_projector_delegates_typed_ack_without_advancing_route() -> None:
    request_payload = _request_payload()
    event = _ack_event(request_payload)
    batch = _RecordingBatchService()
    db = SimpleNamespace()

    await WmsFulfillmentDomainProjector(conveyor_batch=batch).project_event(
        db,
        operation=WMS_OPERATION_BY_IDENTITY[E12],
        request_payload=request_payload,
        event=event,
        reduction=SimpleNamespace(state_changed=True, contradiction=False),
    )

    assert len(batch.ack_calls) == 1
    assert batch.ack_calls[0]["request"].model_dump(mode="json") == request_payload
    assert batch.ack_calls[0]["ack"] == _ack(request_payload)
    assert batch.reject_calls == []


@pytest.mark.asyncio
async def test_e12_initial_typed_ack_projects_after_reducer_in_the_transport_transaction() -> None:
    events: list[str] = []
    request_payload = _request_payload()
    event = _ack_event(request_payload)
    reducer = _RecordingReducer(events)
    projector = _RecordingProjector(events)

    await EffectTransportBridge(reducer=reducer, domain_projector=projector).record_result(
        SimpleNamespace(),
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E12,
        payload_json=request_payload,
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == ["reducer", "projector"]
    assert projector.calls[0]["event"] is event


@pytest.mark.asyncio
async def test_e12_recovered_ack_projects_in_the_status_claim_transaction() -> None:
    events: list[str] = []
    request_payload = _request_payload()
    ack = _ack(request_payload)
    reducer = _RecordingReducer(events)
    projector = _RecordingProjector(events)
    claim = SimpleNamespace(
        intent=SimpleNamespace(dispatch_key=request_payload["dispatch_key"]),
        outbox=SimpleNamespace(
            operation_identity=E12,
            payload_json=request_payload,
            attempt_count=2,
        ),
    )

    await WmsEffectStatusService(
        reducer=reducer,
        domain_projector=projector,
    )._record_recovered_ack(
        SimpleNamespace(),
        claim=claim,
        ack=ack,
        source="status",
    )

    assert events == ["reducer", "projector"]
    assert projector.calls[0]["event"].event_type is EffectReducerEventType.TRANSPORT_ACCEPTED
    assert projector.calls[0]["request_payload"] == request_payload


@pytest.mark.asyncio
async def test_e12_immediate_submit_reject_releases_the_frozen_batch() -> None:
    request_payload = _request_payload()
    batch = _RecordingBatchService()
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=102,
        source_event_id="e12-immediate-reject",
        attempt_no=1,
        reason_code="BATCH_MEMBER_INVALID",
        evidence_json={},
    )

    await WmsFulfillmentDomainProjector(conveyor_batch=batch).project_event(
        SimpleNamespace(),
        operation=WMS_OPERATION_BY_IDENTITY[E12],
        request_payload=request_payload,
        event=event,
        reduction=SimpleNamespace(state_changed=True, contradiction=False),
    )

    assert len(batch.reject_calls) == 1
    assert batch.reject_calls[0]["request"].model_dump(mode="json") == request_payload
    assert batch.ack_calls == []


@pytest.mark.asyncio
async def test_non_e12_typed_ack_does_not_expand_domain_projection_hooks() -> None:
    events: list[str] = []
    request_payload = dict(REQUEST_FIXTURES[E08])
    ack = WmsEffectAck.model_validate(build_typed_ack(E08, "idem-e08", request_payload, submission_state="ACCEPTED"))
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.TRANSPORT_ACCEPTED,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=103,
        source_event_id="e08-ack-event",
        attempt_no=1,
        reason_code="WMS_ASYNC_ACK_ACCEPTED",
        evidence_json={"typed_ack_hash": "b" * 64},
        terminal_outcome={"kind": "success", "payload": ack.model_dump(mode="json")},
    )
    projector = _RecordingProjector(events)

    await EffectTransportBridge(
        reducer=_RecordingReducer(events),
        domain_projector=projector,
    ).record_result(
        SimpleNamespace(),
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E08,
        payload_json=request_payload,
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == ["reducer"]
    assert projector.calls == []


@pytest.mark.asyncio
async def test_non_e12_recovered_ack_does_not_expand_domain_projection_hooks() -> None:
    events: list[str] = []
    request_payload = dict(REQUEST_FIXTURES[E08])
    ack = WmsEffectAck.model_validate(build_typed_ack(E08, "idem-e08", request_payload, submission_state="ACCEPTED"))
    projector = _RecordingProjector(events)
    claim = SimpleNamespace(
        intent=SimpleNamespace(dispatch_key=request_payload["dispatch_key"]),
        outbox=SimpleNamespace(
            operation_identity=E08,
            payload_json=request_payload,
            attempt_count=2,
        ),
    )

    await WmsEffectStatusService(
        reducer=_RecordingReducer(events),
        domain_projector=projector,
    )._record_recovered_ack(
        SimpleNamespace(),
        claim=claim,
        ack=ack,
        source="status",
    )

    assert events == ["reducer"]
    assert projector.calls == []


def test_e12_typed_ack_envelope_rejects_non_object_without_attribute_error() -> None:
    with pytest.raises(TypeError, match="ACK evidence is missing"):
        WmsFulfillmentDomainProjector._typed_ack(
            SimpleNamespace(terminal_outcome=["not", "an", "object"])  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_e12_success_status_terminal_delegates_typed_result_to_batch_service() -> None:
    request_payload = _request_payload()
    ack = _ack(request_payload)
    result_payload = build_typed_result(
        E12,
        request_payload,
        source_version=5,
        completed_at="2026-07-30T09:02:00+00:00",
        provider_reference=ack.provider_reference,
    )
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.STATUS_COMPLETED,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=104,
        source_event_id="e12-success-terminal",
        evidence_json={"snapshot": {"result": result_payload}},
    )
    batch = _RecordingBatchService()

    await WmsFulfillmentDomainProjector(conveyor_batch=batch).project_event(
        SimpleNamespace(),
        operation=WMS_OPERATION_BY_IDENTITY[E12],
        request_payload=request_payload,
        event=event,
        reduction=SimpleNamespace(state_changed=True, contradiction=False),
    )

    assert len(batch.success_calls) == 1
    assert batch.success_calls[0]["request"].model_dump(mode="json") == request_payload
    assert batch.success_calls[0]["result"].model_dump(mode="json") == result_payload


@pytest.mark.asyncio
async def test_e12_non_success_reconciliation_delegates_typed_snapshot_result() -> None:
    request_payload = _request_payload()
    ack = _ack(request_payload)
    result_payload = deepcopy(
        build_typed_result(
            E12,
            request_payload,
            source_version=6,
            completed_at="2026-07-30T09:03:00+00:00",
            provider_reference=ack.provider_reference,
        )
    )
    result_payload["task_outcome"] = "FAILED_AFTER_EXECUTION"
    result_payload["items"][0]["item_outcome"] = "FAILED"
    batch = _RecordingBatchService()
    evidence = {"snapshot": {"result": result_payload}}

    await WmsFulfillmentDomainProjector(conveyor_batch=batch).project_reconciliation_opened(
        SimpleNamespace(),
        operation=WMS_OPERATION_BY_IDENTITY[E12],
        dispatch_key=request_payload["dispatch_key"],
        reason_code="WMS_FULFILLMENT_TERMINAL_NON_SUCCESS",
        evidence_json=evidence,
    )

    assert len(batch.reconciliation_calls) == 1
    assert batch.reconciliation_calls[0]["dispatch_key"] == request_payload["dispatch_key"]
    assert batch.reconciliation_calls[0]["result"].model_dump(mode="json") == result_payload
    assert batch.reconciliation_calls[0]["reason_code"] == "WMS_FULFILLMENT_TERMINAL_NON_SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        EffectReducerEventType.RECONCILIATION_OPENED,
        EffectReducerEventType.IDEMPOTENCY_CONFLICT,
    ],
)
async def test_e12_transport_reconciliation_freezes_domain_after_reducer(
    event_type: EffectReducerEventType,
) -> None:
    events: list[str] = []
    request_payload = _request_payload()
    event = EffectReducerEvent(
        event_type=event_type,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=105,
        source_event_id=f"e12-{event_type.value.lower()}",
        reason_code="IDEMPOTENCY_CONFLICT"
        if event_type is EffectReducerEventType.IDEMPOTENCY_CONFLICT
        else "AMBIGUOUS",
        evidence_json={"operation_identity": E12},
    )
    projector = _RecordingProjector(events)

    await EffectTransportBridge(
        reducer=_RecordingReducer(events),
        domain_projector=projector,
    ).record_result(
        SimpleNamespace(),
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E12,
        payload_json=request_payload,
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == ["reducer", "projector:reconciliation"]
    assert projector.reconciliation_calls[0]["dispatch_key"] == request_payload["dispatch_key"]
    assert projector.reconciliation_calls[0]["reason_code"] == event.reason_code
    assert projector.reconciliation_calls[0]["evidence_json"] == event.evidence_json
