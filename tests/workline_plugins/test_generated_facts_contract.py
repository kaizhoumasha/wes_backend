"""Generated Plugin route facts 的 fail-closed 合同。"""

from __future__ import annotations

from pydantic import BaseModel

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.system_capabilities.outcomes import ContractViolation
from src.app.runtime.workline_plugins.dispatcher import (
    HandlerRegistration,
    PinnedPluginSnapshot,
    PluginAttemptFactSource,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)


class _Config(BaseModel):
    provider_profile: str


class _State(BaseModel):
    phase: str = "READY"


class _Facts(BaseModel):
    binding_snapshot: PinnedPluginSnapshot
    marker: str


def _facts_builder(source: PluginAttemptFactSource) -> dict[str, object]:
    return {"binding_snapshot": source.snapshot, "marker": "FACTS"}


async def _handler(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("facts 校验失败时不得调用 handler")


def _parse_event(payload: dict[str, object]) -> BaseModel:
    return BaseModel.model_validate(payload)


def test_handler_registration_requires_stable_facts_builder() -> None:
    registration = HandlerRegistration(
        handler=_handler,
        facts_model=_Facts,
        facts_builder=_facts_builder,
    )

    assert registration.facts_builder is _facts_builder


async def test_dispatcher_rejects_facts_builder_output_that_misses_facts_model_fields() -> None:
    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

    definition = WorklinePluginDefinition(
        plugin_key="facts_contract",
        contract_version="v1",
        config_model=_Config,
        state_model=_State,
        routes=("EVENT",),
        allowed_capabilities=(),
        parsers={"EVENT": _parse_event},
    )
    snapshot = PinnedPluginSnapshot(
        plugin_key="facts_contract",
        contract_version="v1",
        binding_identity="binding:1:1",
        binding_id=1,
        binding_version=1,
        config_hash=sha256_digest({"provider_profile": "runtime"}),
        index_digest="b" * 64,
        profile_identity="runtime",
    )
    request = PluginDispatchRequest(
        plugin_key="facts_contract",
        contract_version="v1",
        logical_route="EVENT",
        raw_config={"provider_profile": "runtime"},
        raw_state={},
        context_state={},
        raw_input={},
        fact_source=PluginAttemptFactSource(snapshot=snapshot),
        snapshot=snapshot,
    )
    dispatcher = WorklinePluginDispatcher(
        plugin_index={("facts_contract", "v1"): definition},
        plugin_index_digest=snapshot.index_digest,
        handler_registry={
            ("facts_contract", "v1", "EVENT"): (
                HandlerRegistration(
                    handler=_handler,
                    facts_model=_Facts,
                    facts_builder=lambda _source: {"binding_snapshot": snapshot},
                ),
            )
        },
    )

    result = await dispatcher.dispatch(request=request, gateway=object())

    assert isinstance(result, ContractViolation)
    assert result.error_code == "PLUGIN_CONTRACT_INVALID"
