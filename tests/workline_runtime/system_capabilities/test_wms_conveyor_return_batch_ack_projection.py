"""E13 prefix ACK、业务拒绝与恢复 ACK 的领域分派合同。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.app.runtime.orchestration.effect_bridges import EffectTransportBridge, EffectTransportResolution
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEvent, EffectReducerEventType
from src.app.runtime.orchestration.services.wms_conveyor_return_batch_service import (
    WmsConveyorReturnBatchService,
)
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    WmsFulfillmentDomainProjector,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject
from src.app.sys.external_http_transport import ExternalHttpProtocolResult, ExternalHttpTransportResult
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.fulfillment_operations import (
    WmsAcceptedScope,
    WmsEffectAck,
    accepted_scope_digest,
)
from tests.mock.wms_northbound_contract import build_typed_ack
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


class _Reducer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reduced: list[EffectReducerEvent] = []

    async def reduce(self, _db: Any, event: EffectReducerEvent, **_kwargs: Any) -> SimpleNamespace:
        self.events.append(f"reducer:{event.event_type.value}")
        self.reduced.append(event)
        return SimpleNamespace(state_changed=True, contradiction=False, case_created=False)


class _ReturnBatch:
    def __init__(self) -> None:
        self.ack_calls: list[dict[str, Any]] = []
        self.reject_calls: list[dict[str, Any]] = []
        self.reconciliation_calls: list[dict[str, Any]] = []

    async def project_ack(self, db: Any, **kwargs: Any) -> None:
        self.ack_calls.append({"db": db, **kwargs})

    async def project_reject(self, db: Any, **kwargs: Any) -> None:
        self.reject_calls.append({"db": db, **kwargs})

    async def project_reconciliation_opened(self, db: Any, **kwargs: Any) -> None:
        self.reconciliation_calls.append({"db": db, **kwargs})


class _Projector:
    def __init__(
        self,
        events: list[str],
        *,
        ack_conflict: bool = False,
        transport_failure_requires_reconciliation: bool = False,
    ) -> None:
        self.events = events
        self.ack_conflict = ack_conflict
        self.transport_failure_requires_reconciliation = transport_failure_requires_reconciliation
        self.event_calls: list[dict[str, Any]] = []
        self.reconciliation_calls: list[dict[str, Any]] = []
        self.transport_not_sent_calls: list[dict[str, Any]] = []

    async def should_reconcile_ack(self, _db: Any, **_kwargs: Any) -> bool:
        self.events.append("projector:ack-preflight")
        return self.ack_conflict

    async def project_event(self, db: Any, **kwargs: Any) -> None:
        self.events.append("projector:event")
        self.event_calls.append({"db": db, **kwargs})

    async def project_reconciliation_opened(self, db: Any, **kwargs: Any) -> None:
        self.events.append("projector:reconciliation")
        self.reconciliation_calls.append({"db": db, **kwargs})

    async def should_reconcile_transport_failure(self, _db: Any, **_kwargs: Any) -> bool:
        self.events.append("projector:reject-preflight")
        return self.transport_failure_requires_reconciliation

    async def project_transport_not_sent_exhausted(self, db: Any, **kwargs: Any) -> None:
        self.events.append("projector:not-sent-release")
        self.transport_not_sent_calls.append({"db": db, **kwargs})


class _ReplayReducer:
    def __init__(self) -> None:
        self.call_count = 0

    async def reduce(self, _db: Any, _event: EffectReducerEvent, **_kwargs: Any) -> SimpleNamespace:
        self.call_count += 1
        return SimpleNamespace(
            state_changed=self.call_count == 1,
            contradiction=False,
            case_created=False,
        )


class _ResubmitRepository:
    def __init__(self, claim: Any) -> None:
        self.claim = claim
        self.released = 0

    async def get_claim_for_update(self, _db: Any, **_kwargs: Any) -> Any:
        return self.claim

    async def release_claim(self, _db: Any, **_kwargs: Any) -> bool:
        self.released += 1
        return True


class _ReconciliationBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def open(self, db: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"db": db, **kwargs})
        return SimpleNamespace()


class _PhysicalResubmitProjector(_Projector):
    async def should_reconcile_transport_failure(self, _db: Any, **_kwargs: Any) -> bool:
        self.events.append("projector:reject-preflight")
        return True


class _UnchangedReducer(_Reducer):
    async def reduce(self, _db: Any, event: EffectReducerEvent, **_kwargs: Any) -> SimpleNamespace:
        self.events.append(f"reducer:{event.event_type.value}")
        self.reduced.append(event)
        return SimpleNamespace(state_changed=False, contradiction=False, case_created=False)


class _StatusDb:
    async def commit(self) -> None:
        return None


class _PreparedReturnRepository:
    def __init__(self, prepared: Any) -> None:
        self.prepared = prepared

    async def lock_prepared_batch(self, _db: Any, **_kwargs: Any) -> Any:
        return self.prepared


class _FlushDb:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1


def _request_payload() -> dict[str, Any]:
    return dict(REQUEST_FIXTURES[E13])


def _prefix_ack(prefix_count: int = 1) -> WmsEffectAck:
    payload = _request_payload()
    raw = build_typed_ack(E13, "idem-e13", payload, submission_state="ACCEPTED")
    keys = tuple(item["bin_id"] for item in payload["candidate_items"][:prefix_count])
    raw["accepted_scope"] = WmsAcceptedScope(
        object_keys=keys,
        scope_digest=accepted_scope_digest(keys),
    ).model_dump(mode="json")
    return WmsEffectAck.model_validate(raw)


def _ack_event() -> EffectReducerEvent:
    ack = _prefix_ack()
    return EffectReducerEvent(
        event_type=EffectReducerEventType.TRANSPORT_ACCEPTED,
        dispatch_key=_request_payload()["dispatch_key"],
        occurred_at_ms=101,
        source_event_id="e13-prefix-ack",
        attempt_no=1,
        reason_code="WMS_ASYNC_ACK_ACCEPTED",
        evidence_json={"typed_ack_hash": "a" * 64},
        terminal_outcome={"kind": "success", "payload": ack.model_dump(mode="json")},
    )


@pytest.mark.asyncio
async def test_e13_projector_delegates_prefix_ack_and_zero_capacity_reject() -> None:
    batch = _ReturnBatch()
    projector = WmsFulfillmentDomainProjector(conveyor_return_batch=batch)
    request_payload = _request_payload()
    ack_event = _ack_event()
    reject_event = EffectReducerEvent(
        event_type=EffectReducerEventType.ASYNC_SUBMIT_REJECTED,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=102,
        source_event_id="e13-zero-capacity",
        attempt_no=1,
        reason_code="NO_DESTINATION_CAPACITY",
        evidence_json={},
    )

    await projector.project_event(
        SimpleNamespace(),
        operation=WMS_OPERATION_BY_IDENTITY[E13],
        request_payload=request_payload,
        event=ack_event,
        reduction=SimpleNamespace(state_changed=True, contradiction=False),
    )
    await projector.project_event(
        SimpleNamespace(),
        operation=WMS_OPERATION_BY_IDENTITY[E13],
        request_payload=request_payload,
        event=reject_event,
        reduction=SimpleNamespace(state_changed=True, contradiction=False),
    )

    assert batch.ack_calls[0]["ack"] == _prefix_ack()
    assert batch.reject_calls[0]["request"].model_dump(mode="json") == request_payload


@pytest.mark.asyncio
async def test_e13_initial_typed_ack_runs_preflight_and_projection_after_reducer() -> None:
    events: list[str] = []
    event = _ack_event()
    projector = _Projector(events)

    await EffectTransportBridge(reducer=_Reducer(events), domain_projector=projector).record_result(
        SimpleNamespace(),
        dispatch_key=event.dispatch_key,
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E13,
        payload_json=_request_payload(),
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == [
        "reducer:TRANSPORT_ACCEPTED",
        "projector:ack-preflight",
        "projector:event",
    ]


@pytest.mark.asyncio
async def test_e13_committed_ack_replay_skips_preflight_projection_and_reconciliation() -> None:
    events: list[str] = []
    event = _ack_event()
    projector = _Projector(events)
    bridge = EffectTransportBridge(reducer=_ReplayReducer(), domain_projector=projector)
    kwargs = {
        "dispatch_key": event.dispatch_key,
        "attempt_no": 1,
        "result": SimpleNamespace(),
        "retry_exhausted": False,
        "occurred_at_ms": event.occurred_at_ms,
        "operation_identity": E13,
        "payload_json": _request_payload(),
        "resolution": EffectTransportResolution(events=(event,)),
    }

    await bridge.record_result(SimpleNamespace(), **kwargs)
    await bridge.record_result(SimpleNamespace(), **kwargs)

    assert events == ["projector:ack-preflight", "projector:event"]
    assert len(projector.event_calls) == 1
    assert projector.reconciliation_calls == []


@pytest.mark.asyncio
async def test_e13_ack_conflicting_with_local_action_only_opens_reconciliation() -> None:
    events: list[str] = []
    event = _ack_event()
    projector = _Projector(events, ack_conflict=True)

    reductions = await EffectTransportBridge(
        reducer=_Reducer(events),
        domain_projector=projector,
    ).record_result(
        SimpleNamespace(),
        dispatch_key=event.dispatch_key,
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E13,
        payload_json=_request_payload(),
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert len(reductions) == 2
    assert events == [
        "reducer:TRANSPORT_ACCEPTED",
        "projector:ack-preflight",
        "reducer:RECONCILIATION_OPENED",
        "projector:reconciliation",
    ]
    assert projector.event_calls == []


@pytest.mark.asyncio
async def test_e13_status_first_recovered_ack_projects_in_the_status_claim_transaction() -> None:
    events: list[str] = []
    projector = _Projector(events)
    request_payload = _request_payload()
    claim = SimpleNamespace(
        intent=SimpleNamespace(dispatch_key=request_payload["dispatch_key"]),
        outbox=SimpleNamespace(
            operation_identity=E13,
            payload_json=request_payload,
            payload_hash="a" * 64,
            attempt_count=2,
        ),
    )

    result = await WmsEffectStatusService(
        reducer=_Reducer(events),
        domain_projector=projector,
    )._record_recovered_ack(
        SimpleNamespace(),
        claim=claim,
        ack=_prefix_ack(),
        source="status",
    )

    assert result is None
    assert events == [
        "projector:ack-preflight",
        "reducer:TRANSPORT_ACCEPTED",
        "projector:event",
    ]


@pytest.mark.asyncio
async def test_e13_resubmit_business_reject_after_physical_action_only_opens_reconciliation() -> None:
    events: list[str] = []
    request_payload = _request_payload()
    request = WMS_OPERATION_BY_IDENTITY[E13].request_model.model_validate(request_payload)
    claim = SimpleNamespace(
        lease_token="lease-e13",
        intent=SimpleNamespace(
            dispatch_key=request.dispatch_key,
            idempotency_key="idem-e13",
        ),
        outbox=SimpleNamespace(
            operation_identity=E13,
            payload_json=request_payload,
            payload_hash="a" * 64,
            attempt_count=2,
        ),
    )
    repository = _ResubmitRepository(claim)
    reducer = _Reducer(events)
    projector = _PhysicalResubmitProjector(events)
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=repository,
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        domain_projector=projector,
    )
    result = ExternalHttpTransportResult.accepted(
        http_status_code=409,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        protocol_error_code="NO_DESTINATION_CAPACITY",
        response_body=b'{"reason_code":"NO_DESTINATION_CAPACITY"}',
    )
    interpreted = BusinessReject(
        reason_code="NO_DESTINATION_CAPACITY",
        message="return destination has no capacity",
        details={"typed_reject_hash": "b" * 64},
    )

    with (
        patch.object(
            WmsEffectStatusService,
            "_build_request",
            return_value=SimpleNamespace(
                operation_identity=E13,
                request_payload=request_payload,
                frozen_ack=None,
            ),
        ),
        patch(
            "src.app.runtime.orchestration.services.wms_effect_status_service.interpret_async_effect_ack_response",
            return_value=interpreted,
        ),
    ):
        recorded = await service._record_resubmit_result(
            _StatusDb(),
            claim=claim,
            result=result,
            evidence={"recovery": "original-key"},
        )

    assert recorded.outcome == "RECONCILING"
    assert events == ["projector:reject-preflight", "projector:reconciliation"]
    assert reducer.reduced == []
    assert projector.event_calls == []
    assert reconciliation.calls[0]["reason_code"] == "NO_DESTINATION_CAPACITY"
    assert repository.released == 1


@pytest.mark.asyncio
async def test_e13_transport_contract_drift_delegates_reconciliation_without_releasing_claims() -> None:
    events: list[str] = []
    projector = _Projector(events)
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.RECONCILIATION_OPENED,
        dispatch_key=_request_payload()["dispatch_key"],
        occurred_at_ms=103,
        source_event_id="e13-contract-drift",
        reason_code="WMS_ASYNC_ACK_IDENTITY_INVALID",
        evidence_json={"operation_identity": E13},
    )

    await EffectTransportBridge(reducer=_Reducer(events), domain_projector=projector).record_result(
        SimpleNamespace(),
        dispatch_key=event.dispatch_key,
        attempt_no=1,
        result=SimpleNamespace(),
        retry_exhausted=False,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E13,
        payload_json=_request_payload(),
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == ["reducer:RECONCILIATION_OPENED", "projector:reconciliation"]


@pytest.mark.asyncio
async def test_e13_pristine_exhausted_not_sent_releases_even_without_intent_state_change() -> None:
    events: list[str] = []
    projector = _Projector(events)
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.TRANSPORT_NOT_SENT,
        dispatch_key=_request_payload()["dispatch_key"],
        occurred_at_ms=104,
        source_event_id="e13-not-sent",
        attempt_no=2,
        retry_exhausted=True,
        reason_code="CONNECT_TIMEOUT",
        evidence_json={},
    )

    await EffectTransportBridge(reducer=_UnchangedReducer(events), domain_projector=projector).record_result(
        SimpleNamespace(),
        dispatch_key=event.dispatch_key,
        attempt_no=2,
        result=SimpleNamespace(),
        retry_exhausted=True,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E13,
        payload_json=_request_payload(),
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == [
        "reducer:TRANSPORT_NOT_SENT",
        "projector:reject-preflight",
        "projector:not-sent-release",
    ]


@pytest.mark.asyncio
async def test_e13_late_exhausted_not_sent_after_ack_opens_case_even_when_reducer_is_unchanged() -> None:
    events: list[str] = []
    projector = _Projector(events, transport_failure_requires_reconciliation=True)
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.TRANSPORT_NOT_SENT,
        dispatch_key=_request_payload()["dispatch_key"],
        occurred_at_ms=105,
        source_event_id="e13-late-not-sent",
        attempt_no=2,
        retry_exhausted=True,
        reason_code="CONNECT_TIMEOUT",
        evidence_json={},
    )

    await EffectTransportBridge(reducer=_UnchangedReducer(events), domain_projector=projector).record_result(
        SimpleNamespace(),
        dispatch_key=event.dispatch_key,
        attempt_no=2,
        result=SimpleNamespace(),
        retry_exhausted=True,
        occurred_at_ms=event.occurred_at_ms,
        operation_identity=E13,
        payload_json=_request_payload(),
        resolution=EffectTransportResolution(events=(event,)),
    )

    assert events == [
        "reducer:TRANSPORT_NOT_SENT",
        "projector:reject-preflight",
        "reducer:RECONCILIATION_OPENED",
        "projector:reconciliation",
    ]
    assert projector.transport_not_sent_calls == []


@pytest.mark.asyncio
async def test_e13_same_pristine_not_sent_replay_is_domain_noop_without_opening_case() -> None:
    request_payload = _request_payload()
    intent = SimpleNamespace(id=71, dispatch_key=request_payload["dispatch_key"])
    members = []
    routes = []
    memberships = []
    for item in request_payload["candidate_items"]:
        membership_id = 100 + item["sequence_no"]
        members.append(
            SimpleNamespace(
                sequence_no=item["sequence_no"],
                route_instance_id=item["route_instance_id"],
                bin_code=item["bin_id"],
                source_queue_membership_id=membership_id,
                member_state="CANDIDATE",
                reservation_released_at_ms=None,
            )
        )
        routes.append(
            SimpleNamespace(
                route_instance_id=item["route_instance_id"],
                bin_code=item["bin_id"],
                workline_id=request_payload["workline_id"],
                current_node="RETURN_QUEUE",
            )
        )
        memberships.append(
            SimpleNamespace(
                id=membership_id,
                route_instance_id=item["route_instance_id"],
                bin_code=item["bin_id"],
                workline_id=request_payload["workline_id"],
                queue_code=request_payload["queue_code"],
                queue_role="RETURN_QUEUE",
                membership_status="ACTIVE",
                left_at=None,
                e13_claim_intent_id=71,
                e13_claim_token="claim-token",
                e13_claim_until=object(),
            )
        )
    prepared = SimpleNamespace(
        intent=intent,
        outbox=SimpleNamespace(payload_json=request_payload),
        members=tuple(members),
        routes=tuple(routes),
        memberships=tuple(memberships),
    )
    return_batch = WmsConveyorReturnBatchService(repository=_PreparedReturnRepository(prepared))
    projector = WmsFulfillmentDomainProjector(conveyor_return_batch=return_batch)
    events: list[str] = []
    bridge = EffectTransportBridge(reducer=_UnchangedReducer(events), domain_projector=projector)
    event = EffectReducerEvent(
        event_type=EffectReducerEventType.TRANSPORT_NOT_SENT,
        dispatch_key=request_payload["dispatch_key"],
        occurred_at_ms=106,
        source_event_id="e13-not-sent-replay",
        attempt_no=2,
        retry_exhausted=True,
        reason_code="CONNECT_TIMEOUT",
        evidence_json={},
    )
    db = _FlushDb()
    kwargs = {
        "dispatch_key": event.dispatch_key,
        "attempt_no": 2,
        "result": SimpleNamespace(),
        "retry_exhausted": True,
        "occurred_at_ms": event.occurred_at_ms,
        "operation_identity": E13,
        "payload_json": request_payload,
        "resolution": EffectTransportResolution(events=(event,)),
    }

    await bridge.record_result(db, **kwargs)
    await bridge.record_result(db, **kwargs)

    assert all(member.member_state == "RELEASED" for member in members)
    assert all(membership.e13_claim_intent_id is None for membership in memberships)
    assert db.flush_count == 1


def test_e13_reconciling_return_queue_is_not_physical_action() -> None:
    route = SimpleNamespace(current_node="RETURN_QUEUE", lifecycle_state="RECONCILING")
    membership = SimpleNamespace(
        queue_role="RETURN_QUEUE",
        membership_status="RECONCILING",
        left_at=None,
    )

    assert (
        WmsConveyorReturnBatchService.has_observed_physical_action(
            route=route,
            source_membership=membership,
            current_membership=None,
        )
        is False
    )


@pytest.mark.parametrize(
    ("route_node", "membership_status", "left_at", "has_other_membership"),
    [
        ("CTU_RETURN_IN_FLIGHT", "ACTIVE", None, False),
        ("RETURN_QUEUE", "LEFT", 123, False),
        ("RETURN_QUEUE", "ACTIVE", None, True),
    ],
)
def test_e13_physical_action_uses_position_facts_not_reconciliation_state(
    route_node: str,
    membership_status: str,
    left_at: int | None,
    has_other_membership: bool,
) -> None:
    route = SimpleNamespace(current_node=route_node, lifecycle_state="ACTIVE")
    membership = SimpleNamespace(
        queue_role="RETURN_QUEUE",
        membership_status=membership_status,
        left_at=left_at,
    )

    assert (
        WmsConveyorReturnBatchService.has_observed_physical_action(
            route=route,
            source_membership=membership,
            current_membership=SimpleNamespace() if has_other_membership else None,
        )
        is True
    )
