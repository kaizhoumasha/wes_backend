"""同步 WMS EFFECT 的有界 response 与 typed terminal 判别矩阵。"""

from __future__ import annotations

import json
from dataclasses import fields
from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.effect_bridges import (
    EffectTransportAction,
    EffectTransportBridge,
)
from src.app.runtime.orchestration.effect_state_contract import EffectReducerEventType
from src.app.runtime.system_capabilities.outcomes import (
    BusinessReject,
    ContractViolation,
    RetryableFailure,
    Success,
)
from src.app.sys.external_http_transport import (
    MAX_EXTERNAL_HTTP_RESPONSE_BODY_BYTES,
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.services.outbox_engine import SystemOutboxEngine
from src.app.wms_integration import effect_runtime as effect_runtime_module
from src.app.wms_integration.effect_runtime import interpret_sync_effect_response
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS, QUERY_OPERATIONS
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES

SYNC_OPERATIONS = tuple(
    operation for operation in EFFECT_OPERATIONS if operation.completion_mode is WmsCompletionMode.SYNC_RESULT
)
SYNC_IDENTITY_MUTATIONS = (
    ("wms.inventory.reserve_inventory@v1", "material_code", "MAT-DRIFT"),
    ("wms.inventory.reserve_inventory@v1", "reserved_quantity", "11"),
    ("wms.inventory.release_reservation@v1", "reservation_id", "RES-DRIFT"),
    ("wms.inventory.confirm_inbound@v1", "inbound_key", "IN-DRIFT"),
    ("wms.inventory.confirm_outbound@v1", "outbound_key", "OUT-DRIFT"),
    ("wms.inventory.transfer_inventory@v1", "transfer_key", "TRANSFER-DRIFT"),
    ("wms.inventory.confirm_return_putaway@v1", "return_key", "RETURN-DRIFT"),
    ("wms.fulfillment.notify_pkg_binding@v1", "pkg_id", "PKG-DRIFT"),
    ("wms.fulfillment.publish_manual_task@v1", "manual_task_key", "MANUAL-DRIFT"),
    (
        "wms.fulfillment.cancel_request@v1",
        "target_operation_identity",
        "wms.fulfillment.request_rack_supply@v1",
    ),
    ("wms.fulfillment.cancel_request@v1", "target_idempotency_key", "idem-drift"),
    ("wms.fulfillment.cancel_request@v1", "target_provider_reference", "provider-drift"),
)


def _response(status_code: int, payload: object) -> ExternalHttpTransportResult:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    protocol_result = (
        ExternalHttpProtocolResult.ACCEPTED if 200 <= status_code < 300 else ExternalHttpProtocolResult.REJECTED
    )
    return ExternalHttpTransportResult.accepted(
        http_status_code=status_code,
        protocol_result=protocol_result,
        response_body=body,
    )


@pytest.mark.parametrize("status_code", (200, 201))
@pytest.mark.parametrize("operation", SYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_sync_success_and_replay_return_operation_specific_typed_terminal_result(operation, status_code) -> None:
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    outcome = interpret_sync_effect_response(
        operation,
        request,
        _response(status_code, RESULT_FIXTURES[operation.identity]),
    )

    assert outcome == Success(payload=operation.result_model.model_validate(RESULT_FIXTURES[operation.identity]))


@pytest.mark.parametrize("operation", SYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_sync_in_progress_is_retryable_only_with_the_original_submit(operation) -> None:
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    result = _response(
        409,
        {
            "protocol_error_code": "IDEMPOTENCY_REQUEST_IN_PROGRESS",
            "message": "still processing",
        },
    )

    outcome = interpret_sync_effect_response(operation, request, result)

    assert isinstance(outcome, RetryableFailure)
    assert outcome.error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS"


@pytest.mark.parametrize("operation", SYNC_OPERATIONS, ids=lambda operation: operation.identity)
def test_sync_business_reject_uses_only_the_operation_authored_codes(operation) -> None:
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    outcome = interpret_sync_effect_response(
        operation,
        request,
        _response(409, {"reason_code": operation.reject_codes[0], "message": "rejected"}),
    )

    assert isinstance(outcome, BusinessReject)
    assert outcome.reason_code == operation.reject_codes[0]


@pytest.mark.parametrize(
    ("status_code", "payload", "error_code"),
    (
        (422, {"protocol_error_code": "IDEMPOTENCY_CONFLICT"}, "IDEMPOTENCY_CONFLICT"),
        (418, {"reason_code": "UNKNOWN"}, "WMS_SYNC_RESPONSE_UNCLASSIFIED"),
        (200, {"dispatch_key": "incomplete"}, "WMS_MALFORMED_RESPONSE"),
    ),
)
def test_sync_unknown_conflict_and_malformed_responses_fail_closed(status_code, payload, error_code) -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    outcome = interpret_sync_effect_response(operation, request, _response(status_code, payload))

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == error_code


def test_sync_terminal_dispatch_identity_mismatch_fails_closed() -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    payload = {**RESULT_FIXTURES[operation.identity], "dispatch_key": "different-dispatch"}

    outcome = interpret_sync_effect_response(operation, request, _response(200, payload))

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_RESULT_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    ("operation_identity", "result_field", "drifted_value"),
    SYNC_IDENTITY_MUTATIONS,
)
def test_all_sync_definitions_reject_operation_specific_terminal_identity_drift(
    operation_identity,
    result_field,
    drifted_value,
) -> None:
    operation = next(operation for operation in SYNC_OPERATIONS if operation.identity == operation_identity)
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    payload = {
        **RESULT_FIXTURES[operation.identity],
        result_field: drifted_value,
    }

    assert operation.terminal_identity_validator is not None
    outcome = interpret_sync_effect_response(operation, request, _response(200, payload))

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_RESULT_IDENTITY_MISMATCH"


@pytest.mark.parametrize("response_body", (None, b"{", b"[]"))
def test_sync_missing_malformed_or_non_object_body_fails_closed(response_body) -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    result = ExternalHttpTransportResult.accepted(
        http_status_code=200,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        response_body=response_body,
    )

    outcome = interpret_sync_effect_response(operation, request, result)

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_MALFORMED_RESPONSE"


@pytest.mark.parametrize("invalid_operation", ("query", "mismatched-request"))
def test_sync_interpreter_rejects_non_sync_or_mismatched_operation(invalid_operation) -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    if invalid_operation == "query":
        operation = QUERY_OPERATIONS[0]
        request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    else:
        other = SYNC_OPERATIONS[1]
        request = other.request_model.model_validate(REQUEST_FIXTURES[other.identity])

    outcome = interpret_sync_effect_response(operation, request, _response(200, {}))

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_SYNC_OPERATION_INVALID"


def test_sync_stable_code_can_come_from_transport_metadata() -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    result = ExternalHttpTransportResult.accepted(
        http_status_code=409,
        protocol_result=ExternalHttpProtocolResult.REJECTED,
        protocol_error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
        response_body=b"{}",
    )

    outcome = interpret_sync_effect_response(operation, request, result)

    assert isinstance(outcome, RetryableFailure)


def test_sync_response_without_any_stable_code_is_unclassified() -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    outcome = interpret_sync_effect_response(operation, request, _response(418, {}))

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_SYNC_RESPONSE_UNCLASSIFIED"


def test_sync_business_reject_uses_default_message_when_remote_message_is_empty() -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    outcome = interpret_sync_effect_response(
        operation,
        request,
        _response(400, {"reason_code": operation.reject_codes[0], "message": ""}),
    )

    assert isinstance(outcome, BusinessReject)
    assert outcome.message == "WMS rejected the request"


def test_e16_terminal_validator_rejects_a_non_e16_result_model() -> None:
    operation = next(operation for operation in SYNC_OPERATIONS if operation.identity.endswith("cancel_request@v1"))
    other = next(candidate for candidate in SYNC_OPERATIONS if candidate is not operation)
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    mismatched_operation = operation.model_copy(update={"result_model": other.result_model})
    payload = {
        **RESULT_FIXTURES[other.identity],
        "dispatch_key": request.dispatch_key,
    }

    with pytest.raises(TypeError, match="E16 request requires E16 terminal result"):
        effect_runtime_module.validate_effect_terminal_result(mismatched_operation, request, payload)


def test_terminal_result_validation_fails_closed_when_static_validator_is_missing() -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    invalid_operation = operation.model_copy(update={"terminal_identity_validator": None})

    with pytest.raises(ValueError, match="terminal identity validator is missing"):
        effect_runtime_module.validate_effect_terminal_result(
            invalid_operation,
            request,
            RESULT_FIXTURES[operation.identity],
        )


def test_sync_unexpected_terminal_validator_error_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = SYNC_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    def raise_unexpected_error(*_args, **_kwargs):
        raise ValueError("opaque validation failure")

    monkeypatch.setattr(effect_runtime_module, "validate_effect_terminal_result", raise_unexpected_error)

    outcome = interpret_sync_effect_response(
        operation,
        request,
        _response(200, RESULT_FIXTURES[operation.identity]),
    )

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_MALFORMED_RESPONSE"


def test_transport_response_body_is_bounded_in_memory_and_never_enters_evidence_or_repr() -> None:
    body_field = next(field for field in fields(ExternalHttpTransportResult) if field.name == "response_body")
    assert body_field.repr is False

    result = ExternalHttpTransportResult.accepted(
        http_status_code=200,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        response_body=b'{"secret":"must-not-leak"}',
    )
    assert result.response_body == b'{"secret":"must-not-leak"}'
    assert "must-not-leak" not in repr(result)
    assert "response_body" not in result.evidence_json()
    assert b"must-not-leak" not in json.dumps(result.evidence_json()).encode()

    with pytest.raises(ValueError, match="response body"):
        ExternalHttpTransportResult.accepted(
            http_status_code=200,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
            response_body=b"x" * (MAX_EXTERNAL_HTTP_RESPONSE_BODY_BYTES + 1),
        )


class _Reducer:
    def __init__(self) -> None:
        self.events = []

    async def reduce(self, _db, event, *, require_intent=True):
        self.events.append((event, require_intent))
        return event.event_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload_factory", "expected_event"),
    (
        (200, lambda operation: RESULT_FIXTURES[operation.identity], EffectReducerEventType.SYNC_COMPLETED),
        (
            409,
            lambda operation: {"reason_code": operation.reject_codes[0], "message": "rejected"},
            EffectReducerEventType.SYNC_REJECTED,
        ),
    ),
)
async def test_transport_bridge_reduces_sync_terminal_responses_without_status_or_callback_events(
    status_code,
    payload_factory,
    expected_event,
) -> None:
    operation = SYNC_OPERATIONS[0]
    reducer = _Reducer()
    bridge = EffectTransportBridge(reducer=reducer)

    resolution = bridge.resolve_result(
        operation_identity=operation.identity,
        payload_json=REQUEST_FIXTURES[operation.identity],
        result=_response(status_code, payload_factory(operation)),
        dispatch_key=REQUEST_FIXTURES[operation.identity]["dispatch_key"],
        attempt_no=1,
        retry_exhausted=False,
        occurred_at_ms=1,
    )
    reduced = await bridge.record_result(
        object(),
        dispatch_key=REQUEST_FIXTURES[operation.identity]["dispatch_key"],
        attempt_no=1,
        result=_response(status_code, payload_factory(operation)),
        retry_exhausted=False,
        occurred_at_ms=1,
        operation_identity=operation.identity,
        payload_json=REQUEST_FIXTURES[operation.identity],
        resolution=resolution,
    )

    assert resolution.action is EffectTransportAction.DEFAULT
    assert reduced == (expected_event,)
    assert [event.event_type for event, _required in reducer.events] == [expected_event]
    evidence = reducer.events[0][0].evidence_json
    assert evidence["operation_identity"] == operation.identity
    assert "response_body" not in evidence


@pytest.mark.asyncio
async def test_sync_in_progress_requests_outbox_retry_without_advancing_runtime_intent() -> None:
    operation = SYNC_OPERATIONS[0]
    reducer = _Reducer()
    bridge = EffectTransportBridge(reducer=reducer)
    result = _response(
        409,
        {"protocol_error_code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"},
    )
    resolution = bridge.resolve_result(
        operation_identity=operation.identity,
        payload_json=REQUEST_FIXTURES[operation.identity],
        result=result,
        dispatch_key=REQUEST_FIXTURES[operation.identity]["dispatch_key"],
        attempt_no=2,
        retry_exhausted=False,
        occurred_at_ms=2,
    )

    reduced = await bridge.record_result(
        object(),
        dispatch_key=REQUEST_FIXTURES[operation.identity]["dispatch_key"],
        attempt_no=2,
        result=result,
        retry_exhausted=False,
        occurred_at_ms=2,
        operation_identity=operation.identity,
        payload_json=REQUEST_FIXTURES[operation.identity],
        resolution=resolution,
    )

    assert resolution.action is EffectTransportAction.RETRY_SAME_REQUEST
    assert reduced == ()
    assert reducer.events == []


@pytest.mark.parametrize(
    ("result", "expected_event", "expected_reason"),
    (
        (
            _response(422, {"protocol_error_code": "IDEMPOTENCY_CONFLICT"}),
            EffectReducerEventType.IDEMPOTENCY_CONFLICT,
            "IDEMPOTENCY_CONFLICT",
        ),
        (
            _response(200, {"dispatch_key": "incomplete"}),
            EffectReducerEventType.RECONCILIATION_OPENED,
            "WMS_MALFORMED_RESPONSE",
        ),
    ),
)
def test_sync_contract_failures_route_to_conflict_or_reconciliation(
    result,
    expected_event,
    expected_reason,
) -> None:
    operation = SYNC_OPERATIONS[0]
    bridge = EffectTransportBridge(reducer=_Reducer())

    resolution = bridge.resolve_result(
        operation_identity=operation.identity,
        payload_json=REQUEST_FIXTURES[operation.identity],
        result=result,
        dispatch_key=REQUEST_FIXTURES[operation.identity]["dispatch_key"],
        attempt_no=1,
        retry_exhausted=False,
        occurred_at_ms=1,
    )

    assert [event.event_type for event in resolution.events] == [expected_event]
    assert resolution.events[0].reason_code == expected_reason
    assert resolution.action is EffectTransportAction.DEFAULT


def test_sync_bridge_opens_reconciliation_for_invalid_frozen_request() -> None:
    operation = SYNC_OPERATIONS[0]
    bridge = EffectTransportBridge(reducer=_Reducer())

    resolution = bridge.resolve_result(
        operation_identity=operation.identity,
        payload_json={"dispatch_key": "invalid-frozen-request"},
        result=_response(200, RESULT_FIXTURES[operation.identity]),
        dispatch_key="invalid-frozen-request",
        attempt_no=1,
        retry_exhausted=False,
        occurred_at_ms=1,
    )

    assert [event.event_type for event in resolution.events] == [EffectReducerEventType.RECONCILIATION_OPENED]
    assert resolution.events[0].reason_code == "WMS_SYNC_FROZEN_REQUEST_INVALID"


def test_async_submit_can_never_generate_sync_terminal_events() -> None:
    operation = next(operation for operation in EFFECT_OPERATIONS if operation not in SYNC_OPERATIONS)
    bridge = EffectTransportBridge(reducer=_Reducer())

    resolution = bridge.resolve_result(
        operation_identity=operation.identity,
        payload_json=REQUEST_FIXTURES[operation.identity],
        result=_response(202, {"provider_reference": "provider-1"}),
        dispatch_key=REQUEST_FIXTURES[operation.identity]["dispatch_key"],
        attempt_no=1,
        retry_exhausted=False,
        occurred_at_ms=1,
    )

    assert EffectReducerEventType.SYNC_COMPLETED not in {event.event_type for event in resolution.events}
    assert EffectReducerEventType.SYNC_REJECTED not in {event.event_type for event in resolution.events}


@pytest.mark.parametrize(
    ("result", "retry_exhausted", "expected_events", "expected_action"),
    (
        (
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.CONNECTING,
                safe_to_retry=True,
                error_code="CONNECT_ERROR",
            ),
            False,
            (EffectReducerEventType.TRANSPORT_NOT_SENT,),
            EffectTransportAction.DEFAULT,
        ),
        (
            ExternalHttpTransportResult.ambiguous(
                phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
                error_code="READ_TIMEOUT",
            ),
            False,
            (),
            EffectTransportAction.RETRY_SAME_REQUEST,
        ),
        (
            _response(409, {"protocol_error_code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"}),
            True,
            (
                EffectReducerEventType.TRANSPORT_AMBIGUOUS,
                EffectReducerEventType.RECONCILIATION_OPENED,
            ),
            EffectTransportAction.DEFAULT,
        ),
    ),
)
def test_sync_transport_phase_precedes_body_interpretation_and_exhaustion_is_explicit(
    result,
    retry_exhausted,
    expected_events,
    expected_action,
) -> None:
    operation = SYNC_OPERATIONS[0]

    resolution = EffectTransportBridge(reducer=_Reducer()).resolve_result(
        operation_identity=operation.identity,
        payload_json=REQUEST_FIXTURES[operation.identity],
        result=result,
        dispatch_key=REQUEST_FIXTURES[operation.identity]["dispatch_key"],
        attempt_no=3 if retry_exhausted else 1,
        retry_exhausted=retry_exhausted,
        occurred_at_ms=3 if retry_exhausted else 1,
    )

    assert tuple(event.event_type for event in resolution.events) == expected_events
    assert resolution.action is expected_action
    if result.outcome is ExternalHttpTransportOutcome.NOT_SENT:
        assert resolution.events[0].retry_exhausted is False


def test_sync_terminal_event_separates_replay_envelope_from_hash_only_evidence() -> None:
    operation = SYNC_OPERATIONS[0]
    request_payload = REQUEST_FIXTURES[operation.identity]
    expected_payload = RESULT_FIXTURES[operation.identity]

    resolution = EffectTransportBridge(reducer=_Reducer()).resolve_result(
        operation_identity=operation.identity,
        payload_json=request_payload,
        result=_response(200, expected_payload),
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        retry_exhausted=False,
        occurred_at_ms=1,
    )

    event = resolution.events[0]
    serialized_evidence = json.dumps(event.evidence_json, ensure_ascii=False)
    assert event.terminal_outcome == {
        "kind": "success",
        "payload": expected_payload,
    }
    assert event.evidence_json["typed_result_hash"]
    assert event.evidence_json["typed_result_reference"] == f"runtime-intent-outcome:{request_payload['dispatch_key']}"
    assert "typed_outcome" not in event.evidence_json
    assert expected_payload["provider_reference"] not in serialized_evidence


class _OutboxRepository:
    def __init__(self) -> None:
        self.calls = []

    async def mark_as_failed(self, *args, **kwargs):
        self.calls.append(("mark_as_failed", args, kwargs))
        return SimpleNamespace(status="RETRY_WAIT")


@pytest.mark.asyncio
async def test_outbox_finalizer_retries_sync_in_progress_with_the_same_persisted_request() -> None:
    repository = _OutboxRepository()
    engine = SystemOutboxEngine(outbox_repository=repository)

    updated = await engine._finalize_external_http_result(
        object(),
        outbox_id=7,
        result=_response(409, {"protocol_error_code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"}),
        lease_owner_token="lease-1",
        retry_budget=3,
        transport_action=EffectTransportAction.RETRY_SAME_REQUEST,
    )

    assert updated.status == "RETRY_WAIT"
    assert repository.calls[0][0] == "mark_as_failed"
    assert repository.calls[0][1][1] == 7
    assert repository.calls[0][2]["lease_owner_token"] == "lease-1"
