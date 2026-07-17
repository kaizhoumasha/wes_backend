"""Attempt-scoped System Capability QUERY gateway 合同。"""

from __future__ import annotations

import asyncio
from typing import Protocol

import pytest
from pydantic import BaseModel

from src.app.runtime.capability_port_registry import CapabilityPortRegistry, RuntimeCapabilityContext
from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.outcomes import (
    BusinessReject,
    ContractViolation,
    RetryableFailure,
    Success,
)


class QueryInput(BaseModel):
    value: int


class QueryOutput(BaseModel):
    doubled: int


class QueryPort(Protocol):
    async def read(self, value: int) -> int: ...


class FakeQueryPort:
    async def read(self, value: int) -> int:
        return value * 2


class Handler:
    calls = 0
    delay = 0.0
    result: object | None = None
    error: Exception | None = None

    def __init__(self, context: RuntimeCapabilityContext) -> None:
        self._port = context.get_query_port(QueryPort)

    async def __call__(self, request: QueryInput) -> object:
        type(self).calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return QueryOutput(doubled=await self._port.read(request.value))


def definition(*, timeout_seconds: float = 0.1, audit_policy: str = "metadata") -> SystemCapabilityDefinition:
    return SystemCapabilityDefinition(
        capability_key="wms.lookup",
        contract_version="v1",
        mode=SystemCapabilityMode.QUERY,
        input_model=QueryInput,
        output_model=QueryOutput,
        handler_factory=Handler,
        required_ports=(QueryPort,),
        admission="provider-contract",
        timeout_seconds=timeout_seconds,
        completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
        audit_policy=audit_policy,
    )


def gateway(*, attempt_id: str = "attempt-1", **kwargs: object):
    from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway

    registry = CapabilityPortRegistry()
    registry.register(QueryPort, FakeQueryPort)
    return SystemCapabilityGateway(
        attempt_id=attempt_id,
        definitions={("wms.lookup", "v1"): definition()},
        allowed_capabilities=frozenset({("wms.lookup", "v1")}),
        context=RuntimeCapabilityContext(registry),
        admission_profile="provider-contract",
        **kwargs,
    )


@pytest.fixture(autouse=True)
def reset_handler() -> None:
    Handler.calls = 0
    Handler.delay = 0.0
    Handler.result = None
    Handler.error = None


@pytest.mark.asyncio
async def test_query_requires_declared_capability_typed_input_port_and_profile() -> None:
    result = await gateway().execute("wms.lookup", "v1", {"value": 3})
    assert isinstance(result.outcome, Success)
    assert result.outcome.payload == QueryOutput(doubled=6)
    assert result.evidence.capability_key == "wms.lookup"

    undeclared = await gateway().execute("wms.missing", "v1", {"value": 3})
    invalid = await gateway().execute("wms.lookup", "v1", {"value": "bad"})
    assert isinstance(undeclared.outcome, ContractViolation)
    assert isinstance(invalid.outcome, ContractViolation)

    from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway

    missing_port = SystemCapabilityGateway(
        attempt_id="attempt-missing-port",
        definitions={("wms.lookup", "v1"): definition()},
        allowed_capabilities=frozenset({("wms.lookup", "v1")}),
        context=RuntimeCapabilityContext(CapabilityPortRegistry()),
        admission_profile="provider-contract",
    )
    denied_profile = SystemCapabilityGateway(
        attempt_id="attempt-denied-profile",
        definitions={("wms.lookup", "v1"): definition()},
        allowed_capabilities=frozenset({("wms.lookup", "v1")}),
        context=RuntimeCapabilityContext(CapabilityPortRegistry()),
        admission_profile="different-profile",
    )
    assert (await missing_port.execute("wms.lookup", "v1", {"value": 3})).outcome.error_code == (
        "CAPABILITY_CONTRACT_INVALID"
    )
    assert (await denied_profile.execute("wms.lookup", "v1", {"value": 3})).outcome.error_code == (
        "CAPABILITY_ADMISSION_DENIED"
    )


@pytest.mark.asyncio
async def test_query_closes_outcome_contract_timeout_and_unknown_exception() -> None:
    Handler.result = BusinessReject(reason_code="NOT_FOUND", message="missing")
    assert isinstance((await gateway().execute("wms.lookup", "v1", {"value": 1})).outcome, BusinessReject)
    Handler.result = RetryableFailure(error_code="UPSTREAM", message="retry")
    assert isinstance(
        (await gateway(attempt_id="attempt-2").execute("wms.lookup", "v1", {"value": 1})).outcome, RetryableFailure
    )
    Handler.result = ContractViolation(error_code="BAD", message="bad")
    assert isinstance(
        (await gateway(attempt_id="attempt-3").execute("wms.lookup", "v1", {"value": 1})).outcome, ContractViolation
    )

    Handler.result = None
    Handler.error = RuntimeError("provider secret")
    unknown = await gateway(attempt_id="attempt-4").execute("wms.lookup", "v1", {"value": 1})
    assert isinstance(unknown.outcome, RetryableFailure)
    assert unknown.outcome.error_code == "UNKNOWN"
    assert "provider secret" not in unknown.outcome.message

    Handler.error = None
    Handler.delay = 0.02
    timed = gateway(attempt_id="attempt-5")
    timed._definitions[("wms.lookup", "v1")] = definition(timeout_seconds=0.001)
    timeout = await timed.execute("wms.lookup", "v1", {"value": 1})
    assert isinstance(timeout.outcome, RetryableFailure)
    assert timeout.outcome.error_code == "TIMEOUT"


@pytest.mark.asyncio
async def test_same_attempt_coalesces_canonical_query_but_new_attempt_does_not_cache() -> None:
    Handler.delay = 0.01
    scoped = gateway()
    first, second = await asyncio.gather(
        scoped.execute("wms.lookup", "v1", {"value": 2}),
        scoped.execute("wms.lookup", "v1", QueryInput(value=2)),
    )
    assert Handler.calls == 1
    assert first.evidence.input_hash == second.evidence.input_hash

    await gateway(attempt_id="attempt-2").execute("wms.lookup", "v1", {"value": 2})
    assert Handler.calls == 2


@pytest.mark.asyncio
async def test_cancelled_first_waiter_keeps_shared_query_until_handler_finishes() -> None:
    Handler.delay = 0.03
    scoped = gateway()
    first_waiter = asyncio.create_task(scoped.execute("wms.lookup", "v1", {"value": 5}))
    await asyncio.sleep(0.005)
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    result = await scoped.execute("wms.lookup", "v1", {"value": 5})

    assert isinstance(result.outcome, Success)
    assert Handler.calls == 1


@pytest.mark.asyncio
async def test_query_limits_and_redaction_failure_are_fail_closed() -> None:
    from src.app.runtime.extension_identity import canonical_json
    from src.app.runtime.system_capabilities.gateway import GatewayLimits

    limited = gateway(
        limits=GatewayLimits(max_unique_queries=1, max_evidence_bytes=4096, max_total_evidence_bytes=4096)
    )
    await limited.execute("wms.lookup", "v1", {"value": 1})
    overflow = await limited.execute("wms.lookup", "v1", {"value": 2})
    assert isinstance(overflow.outcome, ContractViolation)
    assert overflow.outcome.error_code == "QUERY_LIMIT_EXCEEDED"

    single = await gateway(
        attempt_id="attempt-single-size",
        limits=GatewayLimits(max_unique_queries=2, max_evidence_bytes=1, max_total_evidence_bytes=4096),
    ).execute("wms.lookup", "v1", {"value": 1})
    assert isinstance(single.outcome, ContractViolation)
    assert single.outcome.error_code == "EVIDENCE_ITEM_LIMIT_EXCEEDED"

    probe = await gateway(attempt_id="attempt-size-probe").execute("wms.lookup", "v1", {"value": 1})
    assert probe.evidence is not None
    evidence_size = len(canonical_json(probe.evidence.model_dump(mode="json")).encode())
    total_limited = gateway(
        attempt_id="attempt-total-size",
        limits=GatewayLimits(
            max_unique_queries=2,
            max_evidence_bytes=evidence_size + 10,
            max_total_evidence_bytes=evidence_size + 10,
        ),
    )
    assert (await total_limited.execute("wms.lookup", "v1", {"value": 1})).evidence is not None
    total = await total_limited.execute("wms.lookup", "v1", {"value": 2})
    assert isinstance(total.outcome, ContractViolation)
    assert total.outcome.error_code == "EVIDENCE_TOTAL_LIMIT_EXCEEDED"

    def broken_redactor(_payload: object) -> object:
        raise ValueError("redaction unavailable")

    redaction = await gateway(attempt_id="attempt-redaction", redactor=broken_redactor).execute(
        "wms.lookup", "v1", {"value": 1}
    )
    assert isinstance(redaction.outcome, ContractViolation)
    assert redaction.outcome.error_code == "EVIDENCE_REDACTION_FAILED"
    assert redaction.evidence is None


@pytest.mark.asyncio
async def test_default_metadata_audit_never_records_business_values_or_failure_messages() -> None:
    from src.app.runtime.extension_identity import canonical_json

    Handler.result = Success(payload={"doubled": 2, "password": "sensitive-success"})
    success = await gateway(attempt_id="metadata-success").execute("wms.lookup", "v1", {"value": 1})
    assert success.evidence is not None
    success_summary = canonical_json(success.evidence.summary)
    assert "sensitive-success" not in success_summary
    assert "password" not in success_summary

    Handler.result = BusinessReject(
        reason_code="DENIED",
        message="sensitive-failure-message",
        details={"token": "sensitive-token"},
    )
    failure = await gateway(attempt_id="metadata-failure").execute("wms.lookup", "v1", {"value": 1})
    assert failure.evidence is not None
    failure_summary = canonical_json(failure.evidence.summary)
    assert "sensitive-failure-message" not in failure_summary
    assert "sensitive-token" not in failure_summary
    assert "token" not in failure_summary

    registry = CapabilityPortRegistry()
    registry.register(QueryPort, FakeQueryPort)
    from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway

    unsupported = await SystemCapabilityGateway(
        attempt_id="unsupported-audit",
        definitions={("wms.lookup", "v1"): definition(audit_policy="full-payload")},
        allowed_capabilities=frozenset({("wms.lookup", "v1")}),
        context=RuntimeCapabilityContext(registry),
        admission_profile="provider-contract",
    ).execute("wms.lookup", "v1", {"value": 1})
    assert isinstance(unsupported.outcome, ContractViolation)
    assert unsupported.evidence is None
