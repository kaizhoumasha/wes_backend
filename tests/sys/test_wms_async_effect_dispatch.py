"""7 项 ASYNC_TASK submit ACK 与 status-only terminal 的 RED 合同。"""

from __future__ import annotations

import inspect
import json
from copy import deepcopy

import pytest

from src.app.runtime.orchestration.effect_bridges import EffectTransportBridge
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportResult,
)
from src.app.wms_integration.effect_runtime import typed_wms_effect_ack_hash
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.app.wms_integration.ports.fulfillment_operations import (
    WmsAcceptedScope,
    WmsEffectAck,
    accepted_scope_digest,
)
from tests.mock.wms_northbound_contract import build_typed_ack
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES
from tests.workline_runtime.system_capabilities.test_wms_effect_status_service import (
    NOW,
    _claim,
    _Db,
    _ReconciliationBridge,
    _Reducer,
    _Repository,
    _settings,
)

ASYNC_OPERATIONS = tuple(
    operation for operation in EFFECT_OPERATIONS if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
)
E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"
E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"
TERMINAL_EVENTS = frozenset(
    {
        EffectReducerEventType.SYNC_COMPLETED,
        EffectReducerEventType.SYNC_REJECTED,
        EffectReducerEventType.STATUS_COMPLETED,
        EffectReducerEventType.STATUS_REJECTED,
        EffectReducerEventType.CALLBACK_COMPLETED,
        EffectReducerEventType.CALLBACK_REJECTED,
    }
)


def _transport_result(
    status_code: int,
    payload: object,
    *,
    protocol_error_code: str | None = None,
) -> ExternalHttpTransportResult:
    protocol_result = (
        ExternalHttpProtocolResult.ACCEPTED if 200 <= status_code < 300 else ExternalHttpProtocolResult.REJECTED
    )
    return ExternalHttpTransportResult.accepted(
        http_status_code=status_code,
        protocol_result=protocol_result,
        protocol_error_code=protocol_error_code,
        error_code=None if protocol_result is ExternalHttpProtocolResult.ACCEPTED else "HTTP_REJECTED",
        response_body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _resolve(
    operation,
    request_payload,
    result: ExternalHttpTransportResult,
):
    return EffectTransportBridge().resolve_result(
        operation_identity=operation.identity,
        payload_json=request_payload,
        result=result,
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        retry_exhausted=False,
        occurred_at_ms=1,
        idempotency_key="intent-key",
        payload_hash=CanonicalPayload.from_projection(request_payload).sha256,
    )


def _multi_member_payload(operation_identity: str) -> dict[str, object]:
    payload = deepcopy(REQUEST_FIXTURES[operation_identity])
    field_name = "items" if operation_identity == E12 else "candidate_items"
    first = payload[field_name][0]
    second = {
        **first,
        "sequence_no": 2,
        "route_instance_id": "ROUTE-002",
        "bin_id": "BIN-002",
    }
    if operation_identity == E12:
        second["source_slot_id"] = "SLOT-002"
        second["reserved_queue_position"] = 2
    else:
        second["scan3_enqueued_at"] = "2026-07-29T00:00:01+00:00"
        second["queue_position"] = 2
    payload[field_name] = [first, second]
    if operation_identity == E13:
        # request model 会先校验候选窗口 digest；复用现有 canonical 算法。
        from tests.contracts.wms_integration.test_wms_batch_ack_contract import (
            _candidate_digest,
        )

        payload["candidate_digest"] = _candidate_digest(payload)
    return payload


def test_async_bridge_contract_requires_the_frozen_idempotency_key() -> None:
    signature = inspect.signature(EffectTransportBridge.resolve_result)

    assert "idempotency_key" in signature.parameters


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
@pytest.mark.parametrize(
    ("status_code", "protocol_error_code", "submission_state"),
    (
        (202, None, "ACCEPTED"),
        (409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", "IN_PROGRESS_REPLAY"),
        (200, None, "REPLAY"),
    ),
)
def test_only_authored_async_submit_responses_persist_one_typed_ack_without_terminal_event(
    operation,
    status_code,
    protocol_error_code,
    submission_state,
) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    ack = build_typed_ack(
        operation.identity,
        "intent-key",
        request_payload,
        submission_state=submission_state,
    )

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(
            status_code,
            ack,
            protocol_error_code=protocol_error_code,
        ),
    )

    assert [event.event_type for event in resolution.events] == [EffectReducerEventType.TRANSPORT_ACCEPTED]
    event = resolution.events[0]
    assert event.terminal_outcome == {"kind": "success", "payload": ack}
    assert event.evidence_json["typed_ack_hash"] == typed_wms_effect_ack_hash(WmsEffectAck.model_validate(ack))
    assert event.evidence_json["typed_ack_reference"] == f"runtime-intent-outcome:{request_payload['dispatch_key']}"
    assert "wms_effect_ack" not in event.evidence_json
    assert ack["provider_reference"] not in json.dumps(event.evidence_json, ensure_ascii=False)
    assert TERMINAL_EVENTS.isdisjoint(event.event_type for event in resolution.events)
    assert "response_body" not in event.evidence_json


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
@pytest.mark.parametrize(
    ("status_code", "protocol_error_code", "payload_mutation", "expected_event"),
    (
        (
            422,
            "IDEMPOTENCY_CONFLICT",
            "none",
            EffectReducerEventType.IDEMPOTENCY_CONFLICT,
        ),
        (
            409,
            None,
            "none",
            EffectReducerEventType.RECONCILIATION_OPENED,
        ),
        (
            202,
            None,
            "operation-identity",
            EffectReducerEventType.RECONCILIATION_OPENED,
        ),
        (
            202,
            None,
            "submission-state",
            EffectReducerEventType.RECONCILIATION_OPENED,
        ),
        (
            202,
            None,
            "malformed",
            EffectReducerEventType.RECONCILIATION_OPENED,
        ),
    ),
)
def test_async_conflict_unknown_and_identity_mismatch_fail_closed_without_ack(
    operation,
    status_code,
    protocol_error_code,
    payload_mutation,
    expected_event,
) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    ack = build_typed_ack(
        operation.identity,
        "intent-key",
        request_payload,
        submission_state="ACCEPTED",
    )
    if payload_mutation == "operation-identity":
        other = next(candidate for candidate in ASYNC_OPERATIONS if candidate is not operation)
        ack["operation_identity"] = other.identity
    elif payload_mutation == "submission-state":
        ack["submission_state"] = "REPLAY"
    elif payload_mutation == "malformed":
        ack = {"operation_identity": operation.identity}

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(
            status_code,
            ack,
            protocol_error_code=protocol_error_code,
        ),
    )

    assert expected_event in {event.event_type for event in resolution.events}
    assert all("wms_effect_ack" not in event.evidence_json for event in resolution.events)
    assert TERMINAL_EVENTS.isdisjoint(event.event_type for event in resolution.events)


@pytest.mark.parametrize(
    ("operation_identity", "accepted_keys"),
    (
        (E12, ("BIN-001",)),
        (E13, ("BIN-001", "BIN-003")),
    ),
)
def test_batch_async_ack_enforces_e12_exact_and_e13_ordered_prefix(
    operation_identity,
    accepted_keys,
) -> None:
    operation = next(operation for operation in ASYNC_OPERATIONS if operation.identity == operation_identity)
    request_payload = _multi_member_payload(operation_identity)
    ack = WmsEffectAck(
        operation_identity=operation_identity,
        idempotency_key="intent-key",
        provider_reference="provider-batch-1",
        submission_state="ACCEPTED",
        accepted_scope=WmsAcceptedScope(
            object_keys=accepted_keys,
            scope_digest=accepted_scope_digest(accepted_keys),
        ),
    ).model_dump(mode="json")

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(202, ack),
    )

    assert [event.event_type for event in resolution.events][-1] is EffectReducerEventType.RECONCILIATION_OPENED
    assert resolution.events[-1].reason_code is not None
    assert all("wms_effect_ack" not in event.evidence_json for event in resolution.events)


def test_ack_replay_drift_is_detected_from_append_only_transport_evidence() -> None:
    operation = ASYNC_OPERATIONS[0]
    request_payload = REQUEST_FIXTURES[operation.identity]
    first_ack = build_typed_ack(
        operation.identity,
        "intent-key",
        request_payload,
        submission_state="ACCEPTED",
    )
    replay_ack = {
        **first_ack,
        "provider_reference": "other-provider-reference",
        "submission_state": "REPLAY",
    }
    resolutions = (
        _resolve(operation, request_payload, _transport_result(202, first_ack)),
        _resolve(operation, request_payload, _transport_result(200, replay_ack)),
    )
    history = [
        {
            "event_type": event.event_type.value,
            **event.evidence_json,
        }
        for resolution in resolutions
        for event in resolution.events
        if event.event_type is EffectReducerEventType.TRANSPORT_ACCEPTED
    ]
    authoritative = {
        "payload_hash": CanonicalPayload.from_projection(request_payload).sha256,
        "outcome": resolutions[0].events[0].terminal_outcome,
    }

    with pytest.raises(ValueError, match="ACK evidence drifted"):
        WmsEffectStatusService._load_frozen_ack(
            type(
                "Intent",
                (),
                {
                    "outcome_history_json": history,
                    "outcome_json": authoritative,
                    "payload_hash": authoritative["payload_hash"],
                },
            )()
        )


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_async_submit_terminal_payload_is_rejected_because_only_status_may_complete_intent(
    operation,
) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(200, RESULT_FIXTURES[operation.identity]),
    )

    assert EffectReducerEventType.RECONCILIATION_OPENED in {event.event_type for event in resolution.events}
    assert TERMINAL_EVENTS.isdisjoint(event.event_type for event in resolution.events)
    assert all("wms_effect_ack" not in event.evidence_json for event in resolution.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "protocol_error_code", "submission_state"),
    (
        (200, None, "REPLAY"),
        (409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", "IN_PROGRESS_REPLAY"),
    ),
)
async def test_original_key_status_recovery_accepts_only_the_same_frozen_ack(
    status_code,
    protocol_error_code,
    submission_state,
) -> None:
    claim = _claim()
    ack = {
        **claim.intent.outcome_json["outcome"]["payload"],
        "submission_state": submission_state,
    }
    repository = _Repository(claim)
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=repository,
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service._record_resubmit_result(
        _Db(),
        claim=claim,
        result=_transport_result(
            status_code,
            ack,
            protocol_error_code=protocol_error_code,
        ),
        evidence={"recovery": "original-key"},
    )

    assert result.outcome == "RESUBMITTED"
    assert reconciliation.calls == []


@pytest.mark.asyncio
async def test_original_key_status_recovery_rejects_ack_identity_drift() -> None:
    claim = _claim()
    drifted_ack = {
        **claim.intent.outcome_json["outcome"]["payload"],
        "provider_reference": "other-provider-reference",
        "submission_state": "REPLAY",
    }
    repository = _Repository(claim)
    reconciliation = _ReconciliationBridge()
    service = WmsEffectStatusService(
        repository=repository,
        reducer=_Reducer(),
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service._record_resubmit_result(
        _Db(),
        claim=claim,
        result=_transport_result(200, drifted_ack),
        evidence={"recovery": "original-key"},
    )

    assert result.outcome == "RECONCILING"
    assert reconciliation.calls[0]["reason_code"] == "WMS_STATUS_RESUBMIT_ACK_DRIFT"
