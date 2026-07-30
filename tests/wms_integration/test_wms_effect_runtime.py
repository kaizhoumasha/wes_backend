"""Registry 驱动的共享 WMS EFFECT preparation/handler 合同。"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import (
    SystemCapabilityEffectService,
)
from src.app.runtime.system_capabilities.definition import SystemCapabilityEffectAdmission
from src.app.runtime.system_capabilities.outcomes import ContractViolation, Success
from src.app.runtime.system_capabilities.wms.effect_runtime import (
    WmsEffectDispatchAccepted,
    WmsEffectPreparationResult,
    WmsEffectPreparationRuntime,
    WmsRegistryEffectCapabilityHandler,
    build_wms_effect_capability_definition,
)
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_transport import ExternalHttpProtocolResult, ExternalHttpTransportResult
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.effect_runtime import interpret_async_effect_ack_response
from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsExecutionLane
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS, QUERY_OPERATIONS
from src.core.task_queue_gateway import OutboxDispatchTarget
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.mock.wms_northbound_contract import build_typed_ack
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES


class _PreparationPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def prepare(self, operation, request, *, execution):
        self.calls.append(
            {
                "operation": operation,
                "request": request,
                "execution": execution,
            }
        )
        target = (
            OutboxDispatchTarget.WMS_DATA
            if operation.execution_lane is WmsExecutionLane.WMS_DATA
            else OutboxDispatchTarget.WMS_FULFILLMENT
        )
        return WmsEffectPreparationResult(
            accepted_payload=WmsEffectDispatchAccepted(dispatch_key=request.dispatch_key),
            dispatch_targets=frozenset({target}),
        )


class _IntentService:
    def __init__(self, *, operation, request) -> None:
        from src.app.runtime.system_capabilities.wms.effect_runtime import (
            build_wms_effect_capability_definition,
        )

        self.prepared = SimpleNamespace(
            definition=build_wms_effect_capability_definition(operation),
            request=request,
            admission=SystemCapabilityEffectAdmission(precondition={}, fact_version="fact-1"),
            idempotency_key="idem-1",
            payload_hash="0" * 64,
            claim_result=ClaimResult.NEW,
            claim={"dispatch_key": request.dispatch_key},
            intent_log=SimpleNamespace(dispatch_key=request.dispatch_key),
            has_durable_outbox=False,
        )

    async def prepare_and_claim(self, _ctx, _intent):
        return self.prepared

    async def get_success_evidence(self, _ctx, *, prepared):
        _ = prepared


class _Db:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.parametrize(
    ("invalid_case", "expected_code"),
    (
        ("non-async-operation", "WMS_ASYNC_OPERATION_INVALID"),
        ("payload-fingerprint", "WMS_ASYNC_FROZEN_REQUEST_INVALID"),
        ("frozen-request", "WMS_ASYNC_FROZEN_REQUEST_INVALID"),
        ("malformed-body", "WMS_ASYNC_ACK_MALFORMED"),
    ),
)
def test_async_ack_interpreter_fails_closed_before_persisting_authority(
    invalid_case: str,
    expected_code: str,
) -> None:
    operation = next(
        candidate for candidate in EFFECT_OPERATIONS if candidate.completion_mode is WmsCompletionMode.ASYNC_TASK
    )
    request_payload = deepcopy(REQUEST_FIXTURES[operation.identity])
    ack = build_typed_ack(operation.identity, "intent-key", request_payload, submission_state="ACCEPTED")
    result = ExternalHttpTransportResult.accepted(
        http_status_code=202,
        protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        response_body=json.dumps(ack, ensure_ascii=False, separators=(",", ":")).encode(),
    )
    payload_hash = CanonicalPayload.from_projection(request_payload).sha256
    if invalid_case == "non-async-operation":
        operation = next(
            candidate for candidate in EFFECT_OPERATIONS if candidate.completion_mode is WmsCompletionMode.SYNC_RESULT
        )
    elif invalid_case == "payload-fingerprint":
        payload_hash = "0" * 64
    elif invalid_case == "frozen-request":
        request_payload.pop("dispatch_key")
        payload_hash = CanonicalPayload.from_projection(request_payload).sha256
    else:
        result = ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
            response_body=b"[",
        )

    outcome = interpret_async_effect_ack_response(
        operation,
        request_payload,
        idempotency_key="intent-key",
        payload_hash=payload_hash,
        transport_result=result,
    )

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", EFFECT_OPERATIONS, ids=lambda operation: operation.identity)
async def test_one_shared_handler_delegates_every_effect_to_the_preparation_port(operation) -> None:
    port = _PreparationPort()
    handler = WmsRegistryEffectCapabilityHandler(port)
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    execution = SimpleNamespace(intent_log=object(), idempotency_key=f"idem:{operation.identity}")

    outcome = await handler(request, execution=execution)

    assert outcome.accepted_payload == WmsEffectDispatchAccepted(dispatch_key=request.dispatch_key)
    expected_target = (
        OutboxDispatchTarget.WMS_DATA
        if operation.execution_lane is WmsExecutionLane.WMS_DATA
        else OutboxDispatchTarget.WMS_FULFILLMENT
    )
    assert outcome.dispatch_targets == frozenset({expected_target})
    assert port.calls == [
        {
            "operation": operation,
            "request": request,
            "execution": execution,
        }
    ]


@pytest.mark.asyncio
async def test_shared_handler_fails_closed_when_request_model_is_not_in_the_effect_registry() -> None:
    operation = EFFECT_OPERATIONS[0]
    port = _PreparationPort()
    handler = WmsRegistryEffectCapabilityHandler(port)

    class _UnknownRequest(operation.request_model):
        pass

    request = _UnknownRequest.model_validate(REQUEST_FIXTURES[operation.identity])

    with pytest.raises(ValueError, match="WMS EFFECT request model"):
        await handler(request, execution=SimpleNamespace())

    assert port.calls == []


@pytest.mark.asyncio
async def test_concrete_preparation_runtime_writes_one_frozen_outbox_without_http() -> None:
    operation = EFFECT_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    db = _Db()
    execution = SimpleNamespace(
        db=db,
        ctx={
            "session": SimpleNamespace(id=11),
            "workline": SimpleNamespace(id=22),
            "trace_id": "trace-1",
        },
        intent=SimpleNamespace(operation_key="operation-key-1"),
        intent_log=SimpleNamespace(dispatch_key=request.dispatch_key),
        idempotency_key="idempotency-key-1",
    )

    prepared = await WmsEffectPreparationRuntime(catalog=build_provider_catalog()).prepare(
        operation,
        request,
        execution=execution,
    )

    assert prepared.accepted_payload == WmsEffectDispatchAccepted(dispatch_key=request.dispatch_key)
    assert prepared.dispatch_targets == frozenset({OutboxDispatchTarget.WMS_DATA})
    assert db.flush_count == 1
    assert len(db.added) == 1
    outbox = db.added[0]
    assert isinstance(outbox, SystemOutbox)
    assert outbox.operation_identity == operation.identity
    assert outbox.idempotency_key == "idempotency-key-1"
    assert outbox.payload_json == request.model_dump(mode="json")
    assert outbox.canonical_payload_bytes is not None
    assert outbox.provider_profile_identity == "wms.2026-07-28.full-factory"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected_target_name"),
    (
        (
            next(
                operation
                for operation in EFFECT_OPERATIONS
                if operation.completion_mode is WmsCompletionMode.SYNC_RESULT
                and operation.execution_lane.value == "wms-data"
            ),
            "WMS_DATA",
        ),
        (
            next(
                operation
                for operation in EFFECT_OPERATIONS
                if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
                and operation.domain_projection_kind is None
            ),
            "WMS_FULFILLMENT",
        ),
        (
            next(
                operation
                for operation in EFFECT_OPERATIONS
                if operation.identity == "wms.fulfillment.cancel_request@v1"
            ),
            "WMS_FULFILLMENT",
        ),
    ),
    ids=("data-sync", "fulfillment-async", "fulfillment-sync"),
)
async def test_preparation_returns_transient_public_dispatch_target_from_static_definition(
    operation,
    expected_target_name: str,
) -> None:
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    execution = SimpleNamespace(
        db=_Db(),
        ctx={"session": None, "workline": None, "trace_id": "trace-target"},
        intent=SimpleNamespace(operation_key="operation-target"),
        intent_log=SimpleNamespace(dispatch_key=request.dispatch_key),
        idempotency_key="idempotency-target",
    )

    prepared = await WmsEffectPreparationRuntime(catalog=build_provider_catalog()).prepare(
        operation,
        request,
        execution=execution,
    )

    assert hasattr(prepared, "accepted_payload"), (
        "preparation must separate public accepted payload from transient targets"
    )
    assert prepared.accepted_payload == WmsEffectDispatchAccepted(dispatch_key=request.dispatch_key)
    assert {target.name for target in prepared.dispatch_targets} == {expected_target_name}
    assert "dispatch_targets" not in prepared.accepted_payload.model_dump(mode="json")


def test_effect_capability_builder_rejects_query_operation() -> None:
    with pytest.raises(ValueError, match="requires an EFFECT operation"):
        build_wms_effect_capability_definition(QUERY_OPERATIONS[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid_case", "expected_exception", "message"),
    (
        ("query-operation", ValueError, "requires an EFFECT operation"),
        ("request-model", TypeError, "request does not match"),
        ("missing-intent", RuntimeError, "requires a claimed RuntimeIntentLog"),
        ("dispatch-key", ValueError, "dispatch_key mismatch"),
        ("idempotency-key", ValueError, "persisted idempotency key"),
        ("execution-context", TypeError, "runtime execution context"),
    ),
)
async def test_concrete_preparation_runtime_fails_closed_before_writing_outbox(
    invalid_case,
    expected_exception,
    message,
) -> None:
    operation = EFFECT_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    execution = SimpleNamespace(
        db=_Db(),
        ctx={"session": None, "workline": None},
        intent=SimpleNamespace(operation_key="operation-key-1"),
        intent_log=SimpleNamespace(dispatch_key=request.dispatch_key),
        idempotency_key="idempotency-key-1",
    )
    if invalid_case == "query-operation":
        operation = QUERY_OPERATIONS[0]
        request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    elif invalid_case == "request-model":
        other = EFFECT_OPERATIONS[1]
        request = other.request_model.model_validate(REQUEST_FIXTURES[other.identity])
    elif invalid_case == "missing-intent":
        execution.intent_log = None
    elif invalid_case == "dispatch-key":
        execution.intent_log.dispatch_key = "different-dispatch"
    elif invalid_case == "idempotency-key":
        execution.idempotency_key = " "
    elif invalid_case == "execution-context":
        execution.ctx = None

    with pytest.raises(expected_exception, match=message):
        await WmsEffectPreparationRuntime(catalog=build_provider_catalog()).prepare(
            operation,
            request,
            execution=execution,
        )

    assert execution.db.added == []


@pytest.mark.asyncio
async def test_effect_service_resolves_the_shared_preparation_port_without_changing_outbox_semantics() -> None:
    operation = EFFECT_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    intent_service = _IntentService(operation=operation, request=request)
    port = _PreparationPort()
    service = SystemCapabilityEffectService(
        intent_service=intent_service,
        effect_port_resolver=lambda port_type: port,
    )
    intent = SimpleNamespace(timeout_seconds=None)

    result = await service.apply({"db": SimpleNamespace(flush=lambda: None)}, intent)

    assert result.outcome == Success(payload=WmsEffectDispatchAccepted(dispatch_key=request.dispatch_key))
    assert result.durably_accepted is True
    assert result.remote_completed is False
    assert len(port.calls) == 1


@pytest.mark.asyncio
async def test_effect_service_propagates_only_new_preparation_targets_and_replay_has_none() -> None:
    import src.app.runtime.system_capabilities.wms.effect_runtime as effect_runtime_module
    import src.core.task_queue_gateway as task_queue_module

    preparation_result_type = getattr(effect_runtime_module, "WmsEffectPreparationResult", None)
    target_type = getattr(task_queue_module, "OutboxDispatchTarget", None)
    assert preparation_result_type is not None, "G3 WmsEffectPreparationResult is missing"
    assert target_type is not None, "G3 OutboxDispatchTarget is missing"
    operation = EFFECT_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    class _TargetPreparationPort(_PreparationPort):
        async def prepare(self, prepared_operation, prepared_request, *, execution):
            await super().prepare(prepared_operation, prepared_request, execution=execution)
            return preparation_result_type(
                accepted_payload=WmsEffectDispatchAccepted(dispatch_key=prepared_request.dispatch_key),
                dispatch_targets=frozenset({target_type.WMS_DATA}),
            )

    intent_service = _IntentService(operation=operation, request=request)
    service = SystemCapabilityEffectService(
        intent_service=intent_service,
        effect_port_resolver=lambda _port_type: _TargetPreparationPort(),
    )

    created = await service.apply({"db": SimpleNamespace(flush=lambda: None)}, SimpleNamespace(timeout_seconds=None))

    assert created.outcome == Success(payload=WmsEffectDispatchAccepted(dispatch_key=request.dispatch_key))
    assert created.outbox_dispatch_targets == frozenset({target_type.WMS_DATA})

    intent_service.prepared.claim_result = ClaimResult.MATCH
    intent_service.prepared.has_durable_outbox = True
    intent_service.prepared.intent_log.effect_status = "PROPOSED"
    replay = await service.apply({"db": SimpleNamespace(flush=lambda: None)}, SimpleNamespace(timeout_seconds=None))

    assert replay.idempotent_replay is True
    assert replay.outbox_dispatch_targets == frozenset()


@pytest.mark.asyncio
async def test_effect_service_fails_closed_when_the_required_preparation_port_is_unbound() -> None:
    operation = EFFECT_OPERATIONS[0]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    service = SystemCapabilityEffectService(
        intent_service=_IntentService(operation=operation, request=request),
    )

    result = await service.apply({"db": SimpleNamespace()}, SimpleNamespace(timeout_seconds=None))

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_EFFECT_PORT_UNBOUND"
    assert result.durably_accepted is False
