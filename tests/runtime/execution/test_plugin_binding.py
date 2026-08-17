from dataclasses import dataclass

import pytest
from wes_plugin_sdk import EvidenceReadyFact, FactReference, Wait, handler

from src.app.execution.plugin_binding import (
    InitialExecutionDescriptor,
    PluginRuntimeBinding,
    StaticPluginBinding,
)


@dataclass(frozen=True, slots=True)
class _TypedEvidenceFact(EvidenceReadyFact):
    shape_result: str


@handler(fact_type=_TypedEvidenceFact, name="typed_evidence_ready", supported_versions=("1.0",))
def _handle_typed_evidence(fact: _TypedEvidenceFact) -> tuple[Wait, ...]:
    return (Wait(fact.material_execution_id, fact.fact_id, fact.shape_result),)


class _TypedFactFactory:
    async def build(self, db: object, fact: FactReference) -> FactReference:
        assert db is _FACTORY_DB
        return _TypedEvidenceFact(
            fact_id=fact.fact_id,
            evidence_id=fact.evidence_id,
            fact_version=fact.fact_version,
            material_execution_id=fact.material_execution_id,
            shape_result="PASS",
        )


@handler(fact_type=EvidenceReadyFact, name="evidence_ready", supported_versions=("1.0",))
def _handle_evidence(fact: EvidenceReadyFact) -> tuple[Wait, ...]:
    return (
        Wait(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            reason_code="WAIT",
        ),
    )


@dataclass
class _FakeCorrelator:
    descriptor: InitialExecutionDescriptor | None

    async def correlate(self, evidence_id: str) -> InitialExecutionDescriptor | None:
        assert evidence_id == "1"
        return self.descriptor


class _IdentityFactFactory:
    async def build(self, _db: object, fact: FactReference) -> FactReference:
        return fact


_FACTORY_DB = object()


def _fact(*, version: str = "1.0") -> EvidenceReadyFact:
    return EvidenceReadyFact(
        fact_id="fact-1",
        evidence_id="1",
        fact_version=version,
        material_execution_id="10",
    )


def test_static_binding_resolves_exact_plugin_version_and_fact_type() -> None:
    binding = StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key="rough_sorter",
                plugin_version="1.0.0",
                handlers=(_handle_evidence,),
                fact_factory=_IdentityFactFactory(),
            ),
        )
    )

    assert binding.resolve_handler("rough_sorter", "1.0.0", _fact()) is _handle_evidence
    with pytest.raises(LookupError):
        binding.resolve_handler("rough_sorter", "1.0.1", _fact())
    with pytest.raises(LookupError):
        binding.resolve_handler("rough_sorter", "1.0.0", _fact(version="2.0"))


def test_static_binding_rejects_duplicate_fact_route() -> None:
    with pytest.raises(ValueError, match="duplicate handler route"):
        StaticPluginBinding(
            (
                PluginRuntimeBinding(
                    plugin_key="rough_sorter",
                    plugin_version="1.0.0",
                    handlers=(_handle_evidence, _handle_evidence),
                    fact_factory=_IdentityFactFactory(),
                ),
            )
        )


def test_initial_execution_correlator_is_explicit_and_optional() -> None:
    descriptor = InitialExecutionDescriptor(material_trace_id="trace-1", execution_code="exec-1")
    correlator = _FakeCorrelator(descriptor)
    binding = StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key="rough_sorter",
                plugin_version="1.0.0",
                handlers=(_handle_evidence,),
                fact_factory=_IdentityFactFactory(),
                initial_execution_correlator=correlator,
            ),
        )
    )

    assert binding.resolve_initial_execution_correlator("rough_sorter", "1.0.0") is correlator

    unbound = StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key="other",
                plugin_version="1.0.0",
                handlers=(_handle_evidence,),
                fact_factory=_IdentityFactFactory(),
            ),
        )
    )
    with pytest.raises(LookupError, match="initial execution correlator"):
        unbound.resolve_initial_execution_correlator("other", "1.0.0")


def test_binding_rejects_handler_without_static_metadata() -> None:
    def undecorated(fact: EvidenceReadyFact) -> tuple[Wait, ...]:
        del fact
        return ()

    with pytest.raises(TypeError, match="static metadata"):
        StaticPluginBinding(
            (
                PluginRuntimeBinding(
                    plugin_key="rough_sorter",
                    plugin_version="1.0.0",
                    handlers=(undecorated,),
                    fact_factory=_IdentityFactFactory(),
                ),
            )
        )


@pytest.mark.asyncio
async def test_fact_factory_augments_only_an_immutable_reference_without_raw_payload() -> None:
    factory = _TypedFactFactory()
    binding = StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key="rough_sorter",
                plugin_version="1.0.0",
                handlers=(_handle_typed_evidence,),
                fact_factory=factory,
            ),
        )
    )

    typed_fact = await binding.resolve_fact_factory("rough_sorter", "1.0.0").build(_FACTORY_DB, _fact())

    assert typed_fact == _TypedEvidenceFact(
        fact_id="fact-1",
        evidence_id="1",
        fact_version="1.0",
        material_execution_id="10",
        shape_result="PASS",
    )
    assert binding.resolve_handler("rough_sorter", "1.0.0", typed_fact) is _handle_typed_evidence
    assert not hasattr(typed_fact, "normalized_payload")
