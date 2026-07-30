"""Runtime domain capability 持久化权限链解析测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffDemand
from src.app.runtime.orchestration.repositories.runtime_domain_capability_authority_repository import (
    LockedRuntimeDomainCapabilityFacts,
)
from src.app.runtime.orchestration.services.intent.runtime_domain_capability_authority_resolver import (
    RuntimeDomainCapabilityAuthorityResolver,
)
from src.app.workline.models.workline import LineType, WorkLine

_CORRELATION_ID = "smt-inbound-handoff:17"


class _LockedFactsRepository:
    def __init__(self, facts: LockedRuntimeDomainCapabilityFacts | None) -> None:
        self.facts = facts
        self.calls: list[tuple[object, str]] = []

    async def lock_smt_inbound_handoff_facts(
        self,
        db: object,
        *,
        correlation_id: str,
    ) -> LockedRuntimeDomainCapabilityFacts | None:
        self.calls.append((db, correlation_id))
        return self.facts


def _facts() -> LockedRuntimeDomainCapabilityFacts:
    return LockedRuntimeDomainCapabilityFacts(
        correlation=ExecutionCorrelation(
            id=71,
            correlation_id=_CORRELATION_ID,
            execution_session_id=None,
            trace_id="trace-release-11",
            source_event_id="rack-release-11",
            business_owner_key="handoff-demand:17",
        ),
        demand=SmtInboundHandoffDemand(
            id=17,
            demand_key="handoff-demand:17",
            rack_release_id="rack-release-11",
            source_workline_id=13,
            source_workline_code="SMT-ROUGH-1",
            single_layer_rack_code="SL-RACK-1",
            trace_id="trace-release-11",
        ),
        workline=WorkLine(
            id=13,
            line_code="SMT-ROUGH-1",
            line_name="SMT rough",
            line_type=LineType.AUTO,
        ),
    )


@pytest.mark.asyncio
async def test_resolver_derives_full_authority_only_from_locked_orm_facts() -> None:
    db = object()
    repository = _LockedFactsRepository(_facts())

    resolved = await RuntimeDomainCapabilityAuthorityResolver(repository).resolve(
        db,
        correlation_id=_CORRELATION_ID,
    )

    assert resolved.binding_snapshot == {
        "producer": "SMT_INBOUND_HANDOFF",
        "business_owner_key": "handoff-demand:17",
        "workline_id": 13,
        "correlation_id": _CORRELATION_ID,
    }
    assert repository.calls == [(db, _CORRELATION_ID)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "mutate", "message"),
    [
        (
            "plugin session",
            lambda facts: setattr(facts.correlation, "execution_session_id", 31),
            "plugin session",
        ),
        (
            "unpersisted demand",
            lambda facts: setattr(facts.demand, "id", None),
            "demand is not persisted",
        ),
        (
            "correlation anchor",
            lambda facts: setattr(facts.correlation, "correlation_id", "forged"),
            "correlation anchor mismatch",
        ),
        (
            "business owner",
            lambda facts: setattr(facts.correlation, "business_owner_key", "forged"),
            "business owner mismatch",
        ),
        (
            "release anchor",
            lambda facts: setattr(facts.correlation, "source_event_id", "forged"),
            "release anchor mismatch",
        ),
        (
            "missing trace",
            lambda facts: setattr(facts.demand, "trace_id", None),
            "trace anchor mismatch",
        ),
        (
            "trace anchor",
            lambda facts: setattr(facts.correlation, "trace_id", "forged"),
            "trace anchor mismatch",
        ),
        (
            "workline id",
            lambda facts: setattr(facts.demand, "source_workline_id", 99),
            "workline anchor mismatch",
        ),
        (
            "workline code",
            lambda facts: setattr(facts.demand, "source_workline_code", "forged"),
            "workline anchor mismatch",
        ),
    ],
)
async def test_resolver_rejects_any_persisted_anchor_drift(case, mutate, message: str) -> None:
    facts = _facts()
    mutate(facts)

    with pytest.raises(PermissionError, match=message):
        await RuntimeDomainCapabilityAuthorityResolver(_LockedFactsRepository(facts)).resolve(
            object(),
            correlation_id=_CORRELATION_ID,
        )


@pytest.mark.asyncio
async def test_resolver_rejects_missing_input_or_unresolved_persisted_chain() -> None:
    resolver = RuntimeDomainCapabilityAuthorityResolver(_LockedFactsRepository(_facts()))
    with pytest.raises(ValueError, match="requires correlation_id"):
        await resolver.resolve(object(), correlation_id="")

    unresolved = RuntimeDomainCapabilityAuthorityResolver(_LockedFactsRepository(None))
    with pytest.raises(PermissionError, match="authority is not persisted"):
        await unresolved.resolve(object(), correlation_id=_CORRELATION_ID)
