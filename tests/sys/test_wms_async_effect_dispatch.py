"""4 项 ASYNC_TASK submit ACK 与 status-only terminal 的 RED 合同。"""

from __future__ import annotations

import inspect
import json
from copy import deepcopy

import httpx
import pytest

from src.app.runtime.orchestration.effect_bridges import (
    EffectTransportBridge,
    build_wms_async_submit_reject_event,
)
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.system_capabilities.outcomes import BusinessReject
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportResult,
)
from src.app.sys.services.outbox_engine import _send_external_http
from src.app.wms_integration.effect_runtime import typed_wms_effect_ack_hash
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
from tests.mock.wms_northbound_contract import build_typed_ack
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES
from tests.support.external_http import signed_external_http_request
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
ASYNC_REJECT_CASES = tuple(
    (operation, reason_code) for operation in ASYNC_OPERATIONS for reason_code in operation.reject_codes
)
RESERVED_IDEMPOTENCY_CODES = frozenset({"IDEMPOTENCY_REQUEST_IN_PROGRESS", "IDEMPOTENCY_CONFLICT"})
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


def test_async_bridge_contract_requires_the_frozen_idempotency_key() -> None:
    signature = inspect.signature(EffectTransportBridge.resolve_result)

    assert "idempotency_key" in signature.parameters


def test_async_operation_reject_codes_are_unique_and_exclude_reserved_idempotency_codes() -> None:
    for operation in ASYNC_OPERATIONS:
        assert len(operation.reject_codes) == len(set(operation.reject_codes))
        assert RESERVED_IDEMPOTENCY_CODES.isdisjoint(operation.reject_codes)


@pytest.mark.parametrize("typed_reject_hash", (None, "short", "g" * 64))
def test_async_submit_reject_event_requires_a_canonical_sha256_hash(typed_reject_hash: str | None) -> None:
    with pytest.raises(ValueError, match="validated envelope hash"):
        build_wms_async_submit_reject_event(
            dispatch_key="dispatch-1",
            attempt_no=1,
            occurred_at_ms=1,
            operation_identity=ASYNC_OPERATIONS[0].identity,
            result=_transport_result(422, {}),
            outcome=BusinessReject(
                reason_code=ASYNC_OPERATIONS[0].reject_codes[0],
                message="rejected",
                details={"typed_reject_hash": typed_reject_hash},
            ),
        )


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


@pytest.mark.parametrize(
    ("operation", "reason_code"),
    ASYNC_REJECT_CASES,
    ids=[f"{operation.identity}:{reason_code}" for operation, reason_code in ASYNC_REJECT_CASES],
)
def test_declared_async_business_reject_is_typed_without_ack_or_provider_task(operation, reason_code: str) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    request_fingerprint = CanonicalPayload.from_projection(request_payload).sha256
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": "intent-key",
        "request_fingerprint": request_fingerprint,
        "reason_code": reason_code,
        "message": "WMS rejected before creating a provider task",
    }

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(422, reject),
    )

    assert [event.event_type.value for event in resolution.events] == ["ASYNC_SUBMIT_REJECTED"]
    event = resolution.events[0]
    assert event.reason_code == reason_code
    typed_reject_hash = CanonicalPayload.from_projection(reject).sha256
    assert event.terminal_outcome == {
        "kind": "business_reject",
        "reason_code": reason_code,
        "message": "WMS rejected before creating a provider task",
        "retryable": False,
        "details": {"typed_reject_hash": typed_reject_hash},
    }
    assert event.evidence_json["typed_reject_hash"] == typed_reject_hash
    serialized = json.dumps(event.evidence_json, ensure_ascii=False)
    assert "typed_ack_hash" not in event.evidence_json
    assert "typed_ack_reference" not in event.evidence_json
    assert "accepted_scope" not in serialized
    assert "provider_reference" not in serialized
    assert request_fingerprint not in serialized
    assert "intent-key" not in serialized
    assert "WMS rejected before creating a provider task" not in serialized
    assert {
        "status_check_after",
        "status_source_version",
        "status_binding_snapshot",
    }.isdisjoint(event.evidence_json)


@pytest.mark.asyncio
async def test_real_http_sender_422_strict_body_reject_reaches_bridge_without_injected_protocol_code() -> None:
    operation = ASYNC_OPERATIONS[0]
    request_payload = REQUEST_FIXTURES[operation.identity]
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": "intent-key",
        "request_fingerprint": CanonicalPayload.from_projection(request_payload).sha256,
        "reason_code": operation.reject_codes[0],
        "message": "rejected by real HTTP response",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(422, json=reject, request=request))

    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        result = await _send_external_http(
            signed_external_http_request({"dispatch_key": request_payload["dispatch_key"]}),
            client=client,
        )

    resolution = _resolve(operation, request_payload, result)

    assert result.http_status_code == 422
    assert result.protocol_result is ExternalHttpProtocolResult.REJECTED
    assert result.protocol_error_code is None
    assert [event.event_type for event in resolution.events] == [EffectReducerEventType.ASYNC_SUBMIT_REJECTED]
    assert resolution.events[0].reason_code == operation.reject_codes[0]


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
@pytest.mark.parametrize(
    "drift",
    (
        "operation_identity",
        "idempotency_key",
        "request_fingerprint",
        "reason_code",
        "extra_field",
    ),
)
def test_async_business_reject_identity_or_code_drift_fails_closed(operation, drift: str) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": "intent-key",
        "request_fingerprint": CanonicalPayload.from_projection(request_payload).sha256,
        "reason_code": operation.reject_codes[0],
        "message": "rejected",
    }
    if drift == "operation_identity":
        reject["operation_identity"] = "wms.fulfillment.unknown@v1"
    elif drift == "idempotency_key":
        reject["idempotency_key"] = "other-intent-key"
    elif drift == "request_fingerprint":
        reject["request_fingerprint"] = "0" * 64
    elif drift == "reason_code":
        reject["reason_code"] = "UNDECLARED_REJECT"
    else:
        reject["unexpected"] = "must fail strict validation"

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(422, reject),
    )

    assert "ASYNC_SUBMIT_REJECTED" not in {event.event_type.value for event in resolution.events}
    assert EffectReducerEventType.RECONCILIATION_OPENED in {event.event_type for event in resolution.events}
    assert all(event.terminal_outcome is None for event in resolution.events)


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_async_business_reject_replay_has_stable_hash_only_evidence(operation) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    request_fingerprint = CanonicalPayload.from_projection(request_payload).sha256
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": "intent-key",
        "request_fingerprint": request_fingerprint,
        "reason_code": operation.reject_codes[0],
        "message": "stable reject",
    }
    result = _transport_result(
        422,
        reject,
    )

    first = _resolve(operation, request_payload, result).events[0]
    replay = _resolve(operation, request_payload, result).events[0]

    assert first.source_event_id == replay.source_event_id
    assert first.evidence_json["typed_reject_hash"] == replay.evidence_json["typed_reject_hash"]
    assert first.terminal_outcome == replay.terminal_outcome
    serialized = json.dumps(first.evidence_json, ensure_ascii=False)
    assert request_fingerprint not in serialized
    assert "intent-key" not in serialized
    assert "stable reject" not in serialized


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_async_business_reject_message_drift_keeps_identity_but_changes_evidence_hash(operation) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": "intent-key",
        "request_fingerprint": CanonicalPayload.from_projection(request_payload).sha256,
        "reason_code": operation.reject_codes[0],
        "message": "first reject",
    }
    first = _resolve(
        operation,
        request_payload,
        _transport_result(422, reject),
    ).events[0]
    drifted = _resolve(
        operation,
        request_payload,
        _transport_result(
            422,
            {**reject, "message": "drifted reject"},
        ),
    ).events[0]

    assert first.source_event_id == drifted.source_event_id
    assert first.evidence_json["typed_reject_hash"] != drifted.evidence_json["typed_reject_hash"]
    assert first.terminal_outcome != drifted.terminal_outcome


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_409_with_declared_async_business_reject_code_fails_closed(operation) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": "intent-key",
        "request_fingerprint": CanonicalPayload.from_projection(request_payload).sha256,
        "reason_code": operation.reject_codes[0],
        "message": "wrong HTTP status",
    }

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(
            409,
            reject,
            protocol_error_code=operation.reject_codes[0],
        ),
    )

    assert "ASYNC_SUBMIT_REJECTED" not in {event.event_type.value for event in resolution.events}
    assert EffectReducerEventType.RECONCILIATION_OPENED in {event.event_type for event in resolution.events}
    assert all(event.terminal_outcome is None for event in resolution.events)


@pytest.mark.parametrize("operation", ASYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_422_idempotency_conflict_transport_code_precedes_declared_business_reject(operation) -> None:
    request_payload = REQUEST_FIXTURES[operation.identity]
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": "intent-key",
        "request_fingerprint": CanonicalPayload.from_projection(request_payload).sha256,
        "reason_code": operation.reject_codes[0],
        "message": "must not override the transport contract conflict",
    }

    resolution = _resolve(
        operation,
        request_payload,
        _transport_result(
            422,
            reject,
            protocol_error_code="IDEMPOTENCY_CONFLICT",
        ),
    )

    assert [event.event_type for event in resolution.events] == [
        EffectReducerEventType.TRANSPORT_ACCEPTED,
        EffectReducerEventType.IDEMPOTENCY_CONFLICT,
    ]
    assert resolution.events[-1].reason_code == "IDEMPOTENCY_CONFLICT"
    assert all(event.terminal_outcome is None for event in resolution.events)
    assert EffectReducerEventType.ASYNC_SUBMIT_REJECTED not in {event.event_type for event in resolution.events}


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


@pytest.mark.asyncio
async def test_original_key_status_recovery_records_typed_business_reject_without_repolling() -> None:
    claim = _claim(status=RuntimeIntentStatus.UNKNOWN)
    claim.intent.outcome_history_json = []
    claim.intent.outcome_json = None
    repository = _Repository(claim)
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    db = _Db()
    operation = next(
        operation for operation in ASYNC_OPERATIONS if operation.identity == claim.outbox.operation_identity
    )
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": claim.intent.idempotency_key,
        "request_fingerprint": claim.outbox.payload_hash,
        "reason_code": operation.reject_codes[0],
        "message": "no provider task was created",
    }
    outbox_before = (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at)
    service = WmsEffectStatusService(
        repository=repository,
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service._record_resubmit_result(
        db,
        claim=claim,
        result=_transport_result(
            422,
            reject,
        ),
        evidence={"recovery": "original-key"},
    )

    assert result.outcome == "REJECTED"
    assert [event.event_type for event in reducer.events] == [EffectReducerEventType.ASYNC_SUBMIT_REJECTED]
    assert reducer.events[0].terminal_outcome["kind"] == "business_reject"
    assert repository.released == 1
    assert claim.intent.status_check_after is None
    assert claim.intent.status_check_lease_token is None
    assert db.commits == 1
    assert reconciliation.calls == []
    assert (claim.outbox.status, claim.outbox.attempt_count, claim.outbox.next_retry_at) == outbox_before


@pytest.mark.asyncio
async def test_original_key_business_reject_after_frozen_ack_enters_reconciliation() -> None:
    claim = _claim(status=RuntimeIntentStatus.ACCEPTED)
    repository = _Repository(claim)
    reducer = _Reducer()
    reconciliation = _ReconciliationBridge()
    operation = next(
        operation for operation in ASYNC_OPERATIONS if operation.identity == claim.outbox.operation_identity
    )
    reject = {
        "operation_identity": operation.identity,
        "idempotency_key": claim.intent.idempotency_key,
        "request_fingerprint": claim.outbox.payload_hash,
        "reason_code": operation.reject_codes[0],
        "message": "contradicts the frozen ACK",
    }
    service = WmsEffectStatusService(
        repository=repository,
        reducer=reducer,
        reconciliation_bridge=reconciliation,
        settings_source=_settings(),
        now=lambda: NOW,
        jitter=lambda _upper: 0.0,
    )

    result = await service._record_resubmit_result(
        _Db(),
        claim=claim,
        result=_transport_result(
            422,
            reject,
        ),
        evidence={"recovery": "original-key"},
    )

    assert result.outcome == "RECONCILING"
    assert [event.event_type for event in reducer.events] == [EffectReducerEventType.ASYNC_SUBMIT_REJECTED]
    assert repository.released == 1
    assert claim.intent.status_check_after is None
