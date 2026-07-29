"""SystemCapabilityEffectService 的错误边界与补偿分支。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import (
    SystemCapabilityEffectEvidence,
    SystemCapabilityEffectService,
    SystemCapabilityExecution,
)
from src.app.runtime.system_capabilities.definition import EffectCompletionMode
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.system_capabilities.wms.effect_runtime import (
    WmsEffectDispatchAccepted,
    build_wms_effect_capability_definition,
)
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES


class _PreparedIntentService:
    def __init__(self) -> None:
        operation = EFFECT_OPERATIONS[0]
        request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
        self.prepared = SimpleNamespace(
            definition=build_wms_effect_capability_definition(operation),
            request=request,
            admission=SimpleNamespace(),
            idempotency_key="idem-1",
            payload_hash="0" * 64,
            claim_result=ClaimResult.NEW,
            intent_log=SimpleNamespace(dispatch_key=request.dispatch_key),
            has_durable_outbox=False,
        )

    async def prepare_and_claim(self, _ctx, _intent):
        return self.prepared


class _Port:
    def __init__(self, *, delay: float = 0) -> None:
        self.delay = delay

    async def prepare(self, _operation, request, *, execution):
        if self.delay:
            await asyncio.sleep(self.delay)
        return WmsEffectDispatchAccepted(dispatch_key=request.dispatch_key)


def _intent(*, timeout_seconds: float = 1) -> SimpleNamespace:
    return SimpleNamespace(
        capability_key="wms.inventory.reserve_inventory",
        contract_version="v1",
        operation_key="operation-1",
        timeout_seconds=timeout_seconds,
    )


def _evidence(outcome) -> dict[str, object]:
    return SystemCapabilityEffectEvidence(
        capability_key="test.effect",
        contract_version="v1",
        operation_key="operation-1",
        idempotency_key="idem-1",
        payload_hash="0" * 64,
        outcome_kind=outcome.kind,
        outcome_code=getattr(outcome, "reason_code", None) or getattr(outcome, "error_code", None) or "SUCCESS",
        outcome=outcome.model_dump(mode="json"),
        occurred_at_ms=1,
    ).model_dump(mode="json")


def test_execution_db_property_reads_only_the_scoped_context() -> None:
    db = object()
    execution = SystemCapabilityExecution(
        ctx={"db": db},
        intent=_intent(),
        admission=SimpleNamespace(),
        idempotency_key="idem-1",
        intent_log=None,
    )

    assert execution.db is db


@pytest.mark.asyncio
async def test_effect_apply_maps_timeout_without_flushing() -> None:
    service = SystemCapabilityEffectService(
        intent_service=_PreparedIntentService(),
        effect_port_resolver=lambda _port_type: _Port(delay=0.01),
    )
    db = SimpleNamespace()

    result = await service.apply({"db": db}, _intent(timeout_seconds=0.0001))

    assert isinstance(result.outcome, RetryableFailure)
    assert result.outcome.error_code == "TIMEOUT"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_effect_apply_accepts_success_when_db_has_no_flush_hook() -> None:
    service = SystemCapabilityEffectService(
        intent_service=_PreparedIntentService(),
        effect_port_resolver=lambda _port_type: _Port(),
    )

    result = await service.apply({"db": SimpleNamespace()}, _intent())

    assert isinstance(result.outcome, Success)
    assert result.durably_accepted is True


@pytest.mark.asyncio
async def test_effect_apply_fails_closed_when_resolver_returns_no_port() -> None:
    service = SystemCapabilityEffectService(
        intent_service=_PreparedIntentService(),
        effect_port_resolver=lambda _port_type: None,
    )

    result = await service.apply({"db": SimpleNamespace()}, _intent())

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_EFFECT_PORT_UNBOUND"


class _DefinitionService:
    def __init__(self, definition) -> None:
        self.definition = definition

    def get_effect_definition(self, _capability_key, _contract_version):
        return self.definition


def _compensation_definition(handler) -> SimpleNamespace:
    return SimpleNamespace(
        completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
        output_model=WmsEffectDispatchAccepted,
        handler_factory=lambda: handler,
    )


@pytest.mark.asyncio
async def test_business_reject_compensation_fails_closed_for_invalid_boundaries() -> None:
    reject = BusinessReject(reason_code="BUSINESS_REJECT", message="rejected")
    valid_evidence = _evidence(reject)

    missing_definition = SystemCapabilityEffectService(intent_service=_DefinitionService(None))
    assert await missing_definition.persist_business_reject({}, valid_evidence) is False

    wrong_mode = SimpleNamespace(
        completion_mode=EffectCompletionMode.OUTBOX_ASYNC,
        output_model=WmsEffectDispatchAccepted,
    )
    assert (
        await SystemCapabilityEffectService(intent_service=_DefinitionService(wrong_mode)).persist_business_reject(
            {}, valid_evidence
        )
        is False
    )

    success_evidence = _evidence(Success(payload=WmsEffectDispatchAccepted(dispatch_key="dispatch-1")))
    no_reject = SystemCapabilityEffectService(
        intent_service=_DefinitionService(_compensation_definition(SimpleNamespace()))
    )
    assert await no_reject.persist_business_reject({}, success_evidence) is False
    assert await no_reject.persist_business_reject({}, {"invalid": True}) is False
    assert await no_reject.persist_business_reject({}, valid_evidence) is False

    returns_false = SimpleNamespace(persist_business_reject=lambda _outcome, *, ctx: False)
    false_service = SystemCapabilityEffectService(
        intent_service=_DefinitionService(_compensation_definition(returns_false))
    )
    assert await false_service.persist_business_reject({}, valid_evidence) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", (False, True))
async def test_business_reject_compensation_flushes_after_explicit_true(asynchronous) -> None:
    calls: list[str] = []

    if asynchronous:

        async def compensation(_outcome, *, ctx):
            calls.append("compensated")
            return True

        async def flush():
            calls.append("flushed")

    else:

        def compensation(_outcome, *, ctx):
            calls.append("compensated")
            return True

        def flush():
            calls.append("flushed")

    handler = SimpleNamespace(persist_business_reject=compensation)
    service = SystemCapabilityEffectService(intent_service=_DefinitionService(_compensation_definition(handler)))

    persisted = await service.persist_business_reject(
        {"db": SimpleNamespace(flush=flush)},
        _evidence(BusinessReject(reason_code="BUSINESS_REJECT", message="rejected")),
    )

    assert persisted is True
    assert calls == ["compensated", "flushed"]


@pytest.mark.asyncio
async def test_business_reject_compensation_accepts_db_without_flush_hook() -> None:
    handler = SimpleNamespace(persist_business_reject=lambda _outcome, *, ctx: True)
    service = SystemCapabilityEffectService(intent_service=_DefinitionService(_compensation_definition(handler)))

    persisted = await service.persist_business_reject(
        {"db": SimpleNamespace()},
        _evidence(BusinessReject(reason_code="BUSINESS_REJECT", message="rejected")),
    )

    assert persisted is True


class _ReplayService:
    def __init__(self, evidence) -> None:
        self.evidence = evidence

    async def get_success_evidence(self, _ctx, *, prepared):
        return self.evidence


@pytest.mark.asyncio
async def test_replay_success_rejects_mismatched_identity_and_non_success_outcome() -> None:
    prepared = SimpleNamespace(
        idempotency_key="idem-1",
        payload_hash="0" * 64,
        definition=SimpleNamespace(output_model=WmsEffectDispatchAccepted),
    )
    intent = _intent()
    mismatched = _evidence(Success(payload=WmsEffectDispatchAccepted(dispatch_key="dispatch-1")))
    mismatched["capability_key"] = "different.effect"
    service = SystemCapabilityEffectService(intent_service=_ReplayService(mismatched))

    assert await service._replay_success({}, intent=intent, prepared=prepared) is None

    reject = _evidence(BusinessReject(reason_code="BUSINESS_REJECT", message="rejected"))
    reject.update(
        {
            "capability_key": intent.capability_key,
            "contract_version": intent.contract_version,
            "operation_key": intent.operation_key,
        }
    )
    service = SystemCapabilityEffectService(intent_service=_ReplayService(reject))

    replayed = await service._replay_success({}, intent=intent, prepared=prepared)
    assert replayed is not None
    assert isinstance(replayed[0], BusinessReject)


def test_normalize_outcome_covers_typed_passthrough_and_invalid_payloads() -> None:
    invalid_success = SystemCapabilityEffectService._normalize_outcome(
        Success(payload={}),
        output_model=WmsEffectDispatchAccepted,
    )
    reject = BusinessReject(reason_code="BUSINESS_REJECT", message="rejected")
    passthrough = SystemCapabilityEffectService._normalize_outcome(
        reject,
        output_model=WmsEffectDispatchAccepted,
    )
    invalid_raw = SystemCapabilityEffectService._normalize_outcome(
        {},
        output_model=WmsEffectDispatchAccepted,
    )

    assert isinstance(invalid_success, ContractViolation)
    assert passthrough is reject
    assert isinstance(invalid_raw, ContractViolation)
